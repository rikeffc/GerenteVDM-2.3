import logging
import json
import re
import pdfplumber
import io
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Optional, Tuple

from PyPDF2 import PdfReader
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_
import config
from database.database import get_db, get_or_create_user
from models import Lancamento, Categoria, Subcategoria, Conta, Usuario
from .handlers import cancel, enviar_texto_em_blocos
from .prompts import PROMPT_ANALISE_EXTRATO

logger = logging.getLogger(__name__)

# --- ESTADOS DA CONVERSA ---
(
    AWAIT_EXTRATO_FILE,
    AWAIT_CONTA_ASSOCIADA,
    AWAIT_CONFIRMATION
) = range(900, 903)


class ProcessadorDeDocumentos: # É uma boa prática agrupar funções relacionadas em uma classe

    def _limpar_linha(self, linha: str) -> str:
        """Remove espaços múltiplos e caracteres indesejados de uma linha."""
        # Remove espaços extras entre palavras
        linha_limpa = re.sub(r'\s+', ' ', linha).strip()
        # Remove caracteres que não são letras, números ou pontuação comum
        linha_limpa = re.sub(r'[^\w\s.,/()-R$]', '', linha_limpa)
        return linha_limpa

    def processar_pdf(self, file_bytes: bytes) -> str:
        """
        Extrai texto de um PDF de forma inteligente, tentando preservar a estrutura
        tabular e removendo lixo.
        """
        texto_completo = ""
        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                if not pdf.pages:
                    raise ValueError("PDF sem páginas ou corrompido.")

                for page_num, page in enumerate(pdf.pages):
                    texto_completo += f"\n--- PÁGINA {page_num + 1} ---\n"
                    
                    # Tenta extrair o texto preservando o layout (bom para tabelas)
                    # O layout=True é a chave aqui.
                    texto_pagina = page.extract_text(layout=True, x_tolerance=2, y_tolerance=2)

                    if not texto_pagina:
                        # PLANO B: Se a extração de layout falhar, tenta a extração simples
                        logger.warning(f"Extração com layout falhou na página {page_num + 1}. Tentando método simples.")
                        texto_pagina = page.extract_text()

                    if not texto_pagina:
                        # PLANO C: Se tudo falhar, pode ser uma imagem. (Futuramente, poderia chamar OCR aqui)
                        logger.warning(f"Nenhum texto extraível encontrado na página {page_num + 1}. Pode ser uma imagem.")
                        continue

                    # Limpa cada linha do texto extraído
                    linhas_limpas = [self._limpar_linha(linha) for linha in texto_pagina.split('\n') if self._limpar_linha(linha)]
                    texto_completo += "\n".join(linhas_limpas)

            logger.info(f"PDF processado com sucesso. Total de caracteres extraídos: {len(texto_completo)}")
            return texto_completo

        except Exception as e:
            logger.error(f"Erro CRÍTICO ao processar PDF com pdfplumber: {e}", exc_info=True)
            # Fallback para o método original se o pdfplumber falhar
            logger.info("Tentando fallback com PyPDF2...")
            try:
                from PyPDF2 import PdfReader # Import local para evitar dependência se não for usado
                pdf_reader = PdfReader(io.BytesIO(file_bytes))
                texto_fallback = ""
                for page in pdf_reader.pages:
                    texto_fallback += page.extract_text() or ""
                return texto_fallback
            except Exception as e2:
                logger.error(f"Fallback com PyPDF2 também falhou: {e2}")
                raise ValueError("Não foi possível extrair texto do PDF com nenhum dos métodos.")
    
    def processar_csv(self, file_bytes: bytes) -> List[Dict]:
        """Processa CSV de forma estruturada, detectando automaticamente o formato."""
        try:
            # Tenta diferentes encodings
            encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
            texto_csv = None
            
            for encoding in encodings:
                try:
                    texto_csv = file_bytes.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            
            if not texto_csv:
                raise ValueError("Não foi possível decodificar o arquivo CSV")
            
            # Remove BOM se presente
            if texto_csv.startswith('\ufeff'):
                texto_csv = texto_csv[1:]
            
            # Detecta delimitadores
            delimitadores = [';', ',', '\t', '|']
            melhor_delimitador = ';'
            max_colunas = 0
            
            for delim in delimitadores:
                linhas = texto_csv.split('\n')[:5]  # Testa apenas as primeiras 5 linhas
                total_colunas = sum(len(linha.split(delim)) for linha in linhas if linha.strip())
                if total_colunas > max_colunas:
                    max_colunas = total_colunas
                    melhor_delimitador = delim
            
            # Processa o CSV
            reader = csv.DictReader(
                io.StringIO(texto_csv),
                delimiter=melhor_delimitador,
                quotechar='"',
                skipinitialspace=True
            )
            
            transacoes_estruturadas = []
            
            for linha_num, linha in enumerate(reader, 1):
                if not linha or all(not v.strip() for v in linha.values() if v):
                    continue
                
                # Limpa as chaves (headers)
                linha_limpa = {}
                for key, value in linha.items():
                    if key:
                        key_limpa = key.strip().lower()
                        linha_limpa[key_limpa] = value.strip() if value else ""
                
                # Só adiciona se tiver dados válidos
                if self._linha_tem_dados_validos(linha_limpa):
                    transacoes_estruturadas.append({
                        'linha': linha_num,
                        'dados': linha_limpa
                    })
            
            return transacoes_estruturadas
            
        except Exception as e:
            logger.error(f"Erro ao processar CSV: {e}")
            raise
    
    def processar_ofx(self, file_bytes: bytes) -> str:
        """Processa arquivos OFX."""
        try:
            encodings = ['latin-1', 'utf-8', 'cp1252']
            for encoding in encodings:
                try:
                    return file_bytes.decode(encoding, errors='replace')
                except UnicodeDecodeError:
                    continue
            raise ValueError("Não foi possível decodificar o arquivo OFX")
        except Exception as e:
            logger.error(f"Erro ao processar OFX: {e}")
            raise
    
    def _linha_tem_dados_validos(self, linha: Dict) -> bool:
        """Verifica se a linha tem dados válidos para ser considerada uma transação."""
        # Procura por padrões de data
        padrao_data = re.compile(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}')
        
        # Procura por padrões de valor monetário
        padrao_valor = re.compile(r'[\d.,]+')
        
        tem_data = False
        tem_valor = False
        
        for value in linha.values():
            if padrao_data.search(value):
                tem_data = True
            if padrao_valor.search(value) and len(value.replace(',', '').replace('.', '').replace('-', '')) >= 2:
                tem_valor = True
        
        return tem_data and tem_valor
    
    def extrair_valores_numericos(self, texto: str) -> List[float]:
        """Extrai todos os valores numéricos do texto para validação."""
        # Padrões para valores monetários brasileiros
        padroes = [
            r'R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)',  # R$ 1.234,56
            r'(\d{1,3}(?:\.\d{3})*(?:,\d{2}))',         # 1.234,56
            r'(\d+,\d{2})',                              # 123,45
            r'(\d+\.\d{2})',                             # 123.45
        ]
        
        valores = []
        for padrao in padroes:
            matches = re.findall(padrao, texto)
            for match in matches:
                try:
                    # Converte para float brasileiro
                    valor_str = match.replace('.', '').replace(',', '.')
                    valor = float(valor_str)
                    if valor > 0:  # Só valores positivos
                        valores.append(valor)
                except ValueError:
                    continue
        
        return valores


class ExtratoValidator:
    """Classe para validar dados extraídos do extrato."""
    
    @staticmethod
    def validar_transacao(transacao: Dict) -> Tuple[bool, str]:
        """Valida uma transação individual."""
        campos_obrigatorios = ['data', 'descricao', 'valor']
        
        # Verifica campos obrigatórios
        for campo in campos_obrigatorios:
            if campo not in transacao or not transacao[campo]:
                return False, f"Campo obrigatório '{campo}' ausente"
        
        # Valida data
        try:
            data_str = transacao['data']
            if not re.match(r'\d{1,2}/\d{1,2}/\d{4}', data_str):
                return False, f"Data inválida: {data_str}"
            
            datetime.strptime(data_str, '%d/%m/%Y')
        except ValueError:
            return False, f"Data inválida: {transacao['data']}"
        
        # Valida valor
        try:
            valor = float(transacao['valor'])
            if valor == 0:
                return False, "Valor não pode ser zero"
        except (ValueError, TypeError):
            return False, f"Valor inválido: {transacao['valor']}"
        
        # Valida tipo de transação
        if transacao.get('tipo_transacao') not in ['Entrada', 'Saída']:
            return False, f"Tipo de transação inválido: {transacao.get('tipo_transacao')}"
        
        return True, "Válida"
    
    @staticmethod
    def validar_consistencia_extrato(dados_extrato: Dict, valores_brutos: List[float]) -> Tuple[bool, str]:
        """Valida a consistência geral do extrato."""
        transacoes = dados_extrato.get('transacoes', [])
        
        if not transacoes:
            return False, "Nenhuma transação encontrada"
        
        # Calcula totais
        total_entradas = sum(t['valor'] for t in transacoes if t.get('tipo_transacao') == 'Entrada')
        total_saidas = sum(t['valor'] for t in transacoes if t.get('tipo_transacao') == 'Saída')
        
        # Verifica se os valores fazem sentido com os valores brutos extraídos
        todos_valores_transacoes = [t['valor'] for t in transacoes]
        
        # Pelo menos 50% dos valores devem estar nos valores brutos
        valores_encontrados = 0
        for valor in todos_valores_transacoes:
            if any(abs(valor - v) < 0.01 for v in valores_brutos):
                valores_encontrados += 1
        
        taxa_correspondencia = valores_encontrados / len(todos_valores_transacoes)
        
        if taxa_correspondencia < 0.3:  # Menos de 30% de correspondência
            return False, f"Baixa correspondência de valores ({taxa_correspondencia:.1%})"
        
        return True, f"Consistência OK - {len(transacoes)} transações, correspondência {taxa_correspondencia:.1%}"


# --- FUNÇÕES DO FLUXO ---

async def extrato_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de análise de extrato."""
    await update.message.reply_html(
        "📄 <b>Analisador de Extratos Bancários</b>\n\n"
        "Envie seu extrato em um dos formatos suportados:\n"
        "• <b>PDF</b> - Extratos em formato PDF\n"
        "• <b>CSV</b> - Planilhas com dados estruturados\n"
        "• <b>OFX</b> - Arquivos Open Financial Exchange\n\n"
        "⚡ <b>Processamento Inteligente:</b>\n"
        "• Detecção automática de formato\n"
        "• Validação de dados extraídos\n"
        "• Categorização precisa\n"
        "• Verificação de consistência\n\n"
        "Envie o arquivo e eu cuidarei do resto!"
    )
    return AWAIT_EXTRATO_FILE


async def processar_extrato_arquivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Recebe o arquivo, extrai o texto bruto e envia para a IA para processamento unificado.
    """
    message = await update.message.reply_text("📥 Extrato recebido! Iniciando processamento...")
    
    try:
        file_source = update.message.document
        mime_type = file_source.mime_type
        file_name = file_source.file_name.lower() if file_source.file_name else ''

        await message.edit_text("📥 Baixando arquivo do Telegram...")
        telegram_file = await file_source.get_file()
        file_bytearray = await telegram_file.download_as_bytearray()
        
        texto_bruto = ""
        
        # Extração de texto bruto unificada
        await message.edit_text("🔎 Extraindo texto do documento...")
        if mime_type == 'application/pdf' or file_name.endswith('.pdf'):
            pdf_reader = PdfReader(io.BytesIO(file_bytearray))
            for page in pdf_reader.pages:
                texto_bruto += page.extract_text() or ""
        elif mime_type == 'text/csv' or file_name.endswith('.csv'):
            texto_bruto = file_bytearray.decode('utf-8', errors='replace')
        elif mime_type in ['application/x-ofx', 'text/plain'] or file_name.endswith('.ofx'):
            texto_bruto = file_bytearray.decode('latin-1', errors='replace')
        else:
            await message.edit_text("❌ Formato de arquivo não suportado. Envie um arquivo PDF, CSV ou OFX.")
            return AWAIT_EXTRATO_FILE

        if not texto_bruto or len(texto_bruto.strip()) < 10:
            await message.edit_text("🤔 Não consegui extrair texto válido do arquivo.")
            return ConversationHandler.END

        # Busca categorias para o prompt da IA
        await message.edit_text("📚 Buscando categorias para análise...")
        db: Session = next(get_db())
        try:
            user_db = get_or_create_user(db, update.effective_user.id, update.effective_user.full_name)
            categorias_db = db.query(Categoria).options(joinedload(Categoria.subcategorias)).all()
            categorias_formatadas = [f"- {cat.nome}: ({', '.join(sub.nome for sub in cat.subcategorias)})" for cat in categorias_db]
            categorias_contexto = "\n".join(categorias_formatadas)
        finally:
            db.close()

            # --- LÓGICA DE CHUNKING PARA EVITAR TIMEOUT ---
        await message.edit_text("🧠 Dividindo o documento para análise... Isso pode levar um momento.")
        
        # Divide o texto em pedaços de ~4000 caracteres, quebrando em linhas.
        chunks = []
        current_chunk = ""
        for line in texto_bruto.split('\n'):
            if len(current_chunk) + len(line) + 1 > 4000:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                current_chunk += "\n" + line
        if current_chunk:
            chunks.append(current_chunk)

        todas_as_transacoes = []
        model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)

        for i, chunk in enumerate(chunks):
            await message.edit_text(f"🧠 Analisando parte {i+1} de {len(chunks)} com a IA...")
            
            prompt = PROMPT_ANALISE_EXTRATO.format(
                texto_extrato=chunk,
                categorias_disponiveis=categorias_contexto,
                ano_atual=datetime.now().year,
                nome_usuario=user_db.nome_completo
            )
            
            try:
                ia_response = await model.generate_content_async(prompt)
                response_text = ia_response.text
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                
                if json_match:
                    dados_chunk = json.loads(json_match.group(0))
                    if transacoes_chunk := dados_chunk.get("transacoes"):
                        todas_as_transacoes.extend(transacoes_chunk)
            except Exception as e:
                logger.warning(f"Erro ao processar o chunk {i+1}: {e}. Resposta da IA: {response_text[:200]}")
                continue # Pula para o próximo chunk em caso de erro

        if not todas_as_transacoes:
            await message.edit_text("🤔 A IA não encontrou nenhuma transação válida no extrato.")
            return ConversationHandler.END
        
        # Armazena o resultado final
        context.user_data['dados_extrato'] = {"transacoes": todas_as_transacoes}
        
        # Pergunta a qual conta associar
        await mostrar_selecao_conta(update, message, len(todas_as_transacoes))
        return AWAIT_CONTA_ASSOCIADA
        
    except Exception as e:
        logger.error(f"Erro CRÍTICO no processamento do arquivo de extrato: {e}", exc_info=True)
        await message.edit_text("❌ Ops! Ocorreu um erro inesperado ao processar seu arquivo.")
        return ConversationHandler.END

        # Prompt aprimorado para a IA
        await message.edit_text("🧠 Enviando para análise da IA...")
        model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)
        
        prompt_aprimorado = f"""
ANÁLISE DE EXTRATO BANCÁRIO - {tipo_arquivo}

INSTRUÇÕES CRÍTICAS:
1. Seja EXTREMAMENTE PRECISO na extração de dados
2. Valores devem ser extraídos EXATAMENTE como aparecem
3. Datas devem estar no formato DD/MM/AAAA
4. Categorize com base EXATAMENTE nas categorias fornecidas
5. Tipo de transação: "Entrada" para créditos, "Saída" para débitos

VALORES DETECTADOS NO ARQUIVO (para validação):
{valores_brutos[:20]}  # Primeiros 20 valores

CATEGORIAS DISPONÍVEIS:
{categorias_contexto}

DADOS DO EXTRATO:
{texto_bruto}

RETORNE UM JSON VÁLIDO com esta estrutura EXATA:
{{
    "metadados": {{
        "tipo_arquivo": "{tipo_arquivo}",
        "total_linhas_processadas": 0,
        "periodo_inicio": "DD/MM/AAAA",
        "periodo_fim": "DD/MM/AAAA"
    }},
    "transacoes": [
        {{
            "data": "DD/MM/AAAA",
            "descricao": "Descrição exata da transação",
            "valor": 0.00,
            "tipo_transacao": "Entrada" ou "Saída",
            "categoria_sugerida": "Nome da categoria",
            "subcategoria_sugerida": "Nome da subcategoria",
            "observacoes": "Observações relevantes se houver"
        }}
    ]
}}
"""
        
        ia_response = await model.generate_content_async(prompt_aprimorado)
        
        # Extrai e valida JSON
        response_text = ia_response.text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            logger.error(f"Nenhum JSON encontrado na resposta da IA")
            await message.edit_text("❌ IA não retornou dados válidos. Tente outro arquivo.")
            return AWAIT_EXTRATO_FILE

        try:
            dados_extrato = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar JSON: {e}")
            await message.edit_text("❌ Resposta da IA em formato inválido. Tente novamente.")
            return AWAIT_EXTRATO_FILE

        # Validação rigorosa
        await message.edit_text("✅ Validando dados extraídos...")
        validator = ExtratoValidator()
        
        transacoes = dados_extrato.get('transacoes', [])
        if not transacoes:
            await message.edit_text("❌ Nenhuma transação válida encontrada.")
            return AWAIT_EXTRATO_FILE
        
        # Valida cada transação
        transacoes_validas = []
        erros_validacao = []
        
        for i, transacao in enumerate(transacoes):
            valida, motivo = validator.validar_transacao(transacao)
            if valida:
                transacoes_validas.append(transacao)
            else:
                erros_validacao.append(f"Transação {i+1}: {motivo}")
        
        if not transacoes_validas:
            await message.edit_text(
                f"❌ Nenhuma transação válida encontrada.\n\n"
                f"Erros:\n" + "\n".join(erros_validacao[:5])
            )
            return AWAIT_EXTRATO_FILE
        
        # Validação de consistência
        dados_extrato['transacoes'] = transacoes_validas
        consistente, motivo_consistencia = validator.validar_consistencia_extrato(dados_extrato, valores_brutos)
        
        if not consistente:
            await message.edit_text(f"⚠️ Problema de consistência: {motivo_consistencia}")
            # Mas continua o processo para o usuário decidir
        
        # Salva os dados validados
        context.user_data['dados_extrato'] = dados_extrato
        context.user_data['info_validacao'] = {
            'total_original': len(transacoes),
            'total_validas': len(transacoes_validas),
            'erros': erros_validacao,
            'consistencia': motivo_consistencia
        }
        
        # Mostra seleção de conta
        await mostrar_selecao_conta(update, message, len(transacoes_validas))
        return AWAIT_CONTA_ASSOCIADA
        
    except Exception as e:
        logger.error(f"Erro crítico no processamento: {e}", exc_info=True)
        await message.edit_text(f"❌ Erro inesperado: {str(e)}")
        return AWAIT_EXTRATO_FILE


async def mostrar_selecao_conta(update: Update, message, num_transacoes: int):
    """Mostra opções de conta para associar o extrato."""
    db = next(get_db())
    try:
        user_db = get_or_create_user(db, update.effective_user.id, update.effective_user.full_name)
        contas = db.query(Conta).filter(
            Conta.id_usuario == user_db.id,
            Conta.tipo != 'Cartão de Crédito'
        ).all()

        if not contas:
            await message.edit_text("Você não tem contas cadastradas. Use `/configurar` para adicionar uma.")
            return

        botoes = [[InlineKeyboardButton(c.nome, callback_data=f"extrato_conta_{c.id}")] for c in contas]
        await message.edit_text(
            f"🏦 Análise concluída! Encontrei <b>{num_transacoes}</b> transações.\n\n"
            "A qual das suas contas este extrato pertence?",
            reply_markup=InlineKeyboardMarkup(botoes),
            parse_mode='HTML'
        )
    finally:
        db.close()


async def associar_conta_e_confirmar_extrato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Mostra o resumo do extrato para confirmação, calculando os totais aqui.
    """
    query = update.callback_query
    await query.answer()
    
    conta_id = int(query.data.split('_')[-1])
    context.user_data['conta_id_extrato'] = conta_id

    dados_extrato = context.user_data.get('dados_extrato', {})
    transacoes = dados_extrato.get('transacoes', [])

    if not transacoes:
        await query.edit_message_text("Não foram encontradas transações válidas no extrato.")
        return ConversationHandler.END

    # --- CÁLCULO FEITO AQUI NO PYTHON, NÃO PELA IA ---
    total_entradas = sum(float(t.get('valor', 0.0)) for t in transacoes if t.get('tipo_transacao') == 'Entrada')
    total_saidas = sum(float(t.get('valor', 0.0)) for t in transacoes if t.get('tipo_transacao') == 'Saída')
    
    # Monta a prévia das transações (lógica que já tínhamos)
    lista_transacoes_str = []
    for t in transacoes:
        emoji = "🟢" if t.get('tipo_transacao') == 'Entrada' else "🔴"
        data_str = t.get('data', 'N/D')
        desc = t.get('descricao', 'N/A')
        valor = float(t.get('valor', 0.0))
        lista_transacoes_str.append(f"{emoji} <code>{data_str}</code> - {desc[:30]:<30} <b>R$ {valor:>7.2f}</b>")

    # Deleta a mensagem anterior
    await query.message.delete()

    # Envia a lista completa
    cabecalho = "<b>Revisão das Transações Encontradas:</b>\n\n"
    corpo_lista = "\n".join(lista_transacoes_str)
    # A função enviar_texto_em_blocos precisa ser importada de handlers.py
    await enviar_texto_em_blocos(context.bot, update.effective_chat.id, cabecalho + corpo_lista)

    # Envia a mensagem final de confirmação com os totais CALCULADOS
    texto_confirmacao = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>Resumo da Importação:</b>\n"
        f"📄 Transações encontradas: <b>{len(transacoes)}</b>\n"
        f"🟢 Total de Entradas: <code>R$ {total_entradas:.2f}</code>\n"
        f"🔴 Total de Saídas: <code>R$ {total_saidas:.2f}</code>\n\n"
        "Deseja importar todas essas movimentações para a conta selecionada?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Sim, importar tudo", callback_data="extrato_confirm_save")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="extrato_confirm_cancel")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=texto_confirmacao,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return AWAIT_CONFIRMATION



async def salvar_transacoes_extrato_em_lote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva todas as transações do extrato no banco de dados."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💾 Verificando e salvando no banco de dados...")

    dados_extrato = context.user_data.get('dados_extrato')
    conta_id = context.user_data.get('conta_id_extrato')

    if not dados_extrato or not conta_id:
        await query.edit_message_text("❌ Erro: Dados da sessão perdidos. Operação cancelada.")
        return ConversationHandler.END

    db: Session = next(get_db())
    try:
        user_info = query.from_user
        usuario_db = get_or_create_user(db, user_info.id, user_info.full_name)
        conta_selecionada = db.query(Conta).filter(Conta.id == conta_id).one()

        categorias_map = {cat.nome.lower(): cat.id for cat in db.query(Categoria).all()}
        subcategorias_map = {(sub.id_categoria, sub.nome.lower()): sub.id for sub in db.query(Subcategoria).all()}

        novos_lancamentos = []
        duplicatas_ignoradas = 0
        transacoes_para_salvar = dados_extrato.get('transacoes', [])
        
        for transacao in transacoes_para_salvar:
            try:
                data_obj = datetime.strptime(transacao['data'], '%d/%m/%Y')
                valor = float(transacao['valor'])
                descricao = transacao.get('descricao', 'Transação de Extrato').strip()
                
                lancamento_existente = db.query(Lancamento).filter(
                    and_(
                        Lancamento.id_usuario == usuario_db.id,
                        Lancamento.id_conta == conta_id,
                        Lancamento.data_transacao == data_obj,
                        Lancamento.descricao.ilike(f'%{descricao}%'),
                        Lancamento.valor == valor,
                        Lancamento.tipo == transacao.get('tipo_transacao')
                    )
                ).first()
                
                if lancamento_existente:
                    duplicatas_ignoradas += 1
                    continue
                
                cat_nome = transacao.get('categoria_sugerida', '').lower().strip()
                id_categoria = categorias_map.get(cat_nome)
                
                id_subcategoria = None
                if id_categoria:
                    sub_nome = transacao.get('subcategoria_sugerida', '').lower().strip()
                    id_subcategoria = subcategorias_map.get((id_categoria, sub_nome))

                novo_lancamento = Lancamento(
                    id_usuario=usuario_db.id,
                    descricao=descricao,
                    valor=valor,
                    tipo=transacao.get('tipo_transacao', 'Saída'),
                    data_transacao=data_obj,
                    id_conta=conta_id,
                    forma_pagamento=conta_selecionada.nome,
                    id_categoria=id_categoria,
                    id_subcategoria=id_subcategoria,
                    observacoes=transacao.get('observacoes', '')
                )
                novos_lancamentos.append(novo_lancamento)
            except Exception as e:
                logger.error(f"Erro ao processar transação individual: {transacao} | Erro: {e}")
                continue

        if novos_lancamentos:
            db.add_all(novos_lancamentos)
            db.commit()
        
        await query.edit_message_text(
            f"✅ Importação Concluída!\n\n"
            f"• Novas transações salvas: <b>{len(novos_lancamentos)}</b>\n"
            f"• Duplicatas ignoradas: <b>{duplicatas_ignoradas}</b>",
            parse_mode='HTML'
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar transações em lote: {e}", exc_info=True)
        await query.edit_message_text("❌ Ocorreu um erro grave ao tentar salvar as transações.")
    finally:
        db.close()
        context.user_data.clear()

    return ConversationHandler.END


async def cancelar_extrato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela o processo de importação."""
    query = update.callback_query
    await query.answer()
    
    # Limpa dados da sessão
    context.user_data.pop('dados_extrato', None)
    context.user_data.pop('conta_id_extrato', None)
    context.user_data.pop('info_validacao', None)
    
    await query.edit_message_text("❌ Importação cancelada.")
    return ConversationHandler.END


# --- CONVERSATION HANDLER ---

def criar_conversation_handler_extrato():
    """Cria o ConversationHandler para análise de extratos."""
    return ConversationHandler(
        entry_points=[CommandHandler('extrato', extrato_start)],
        states={
            AWAIT_EXTRATO_FILE: [
                MessageHandler(filters.Document.ALL, processar_extrato_arquivo),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "Por favor, envie um arquivo de extrato (PDF, CSV ou OFX)."
                ))
            ],
            AWAIT_CONTA_ASSOCIADA: [
                CallbackQueryHandler(associar_conta_e_confirmar_extrato, pattern=r'^extrato_conta_\d+$')
            ],
            AWAIT_CONFIRMATION: [
                CallbackQueryHandler(salvar_transacoes_extrato_em_lote, pattern='^extrato_confirm_save$'),
                CallbackQueryHandler(cancelar_extrato, pattern='^extrato_confirm_cancel$')
            ]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CallbackQueryHandler(cancel, pattern='^cancel$')
        ],
        name="extrato_handler",
        persistent=False
    )


# --- FUNÇÕES AUXILIARES ---

def normalizar_texto_extrato(texto: str) -> str:
    """Normaliza texto do extrato para melhor processamento."""
    # Remove caracteres especiais desnecessários
    texto = re.sub(r'[^\w\s\-\.\,\:\;\(\)\[\]\/\+\=\@\#\$\%\&\*]', ' ', texto)
    
    # Normaliza espaços
    texto = re.sub(r'\s+', ' ', texto)
    
    # Remove linhas muito curtas (provavelmente ruído)
    linhas = texto.split('\n')
    linhas_filtradas = [linha for linha in linhas if len(linha.strip()) > 5]
    
    return '\n'.join(linhas_filtradas)


def detectar_formato_data(texto: str) -> str:
    """Detecta o formato de data mais comum no texto."""
    padroes = {
        'dd/mm/yyyy': r'\d{1,2}/\d{1,2}/\d{4}',
        'dd-mm-yyyy': r'\d{1,2}-\d{1,2}-\d{4}',
        'yyyy-mm-dd': r'\d{4}-\d{1,2}-\d{1,2}',
        'dd.mm.yyyy': r'\d{1,2}\.\d{1,2}\.\d{4}'
    }
    
    contadores = {}
    for formato, padrao in padroes.items():
        matches = re.findall(padrao, texto)
        contadores[formato] = len(matches)
    
    return max(contadores, key=contadores.get) if contadores else 'dd/mm/yyyy'


def extrair_periodo_extrato(texto: str) -> Tuple[Optional[str], Optional[str]]:
    """Extrai período do extrato (data inicial e final)."""
    # Padrões comuns de período em extratos
    padroes_periodo = [
        r'(?:período|periodo|extrato).*?(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}).*?(?:a|até|-).*?(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})',
        r'(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}).*?(?:a|até|-).*?(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})'
    ]
    
    for padrao in padroes_periodo:
        match = re.search(padrao, texto, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    
    # Se não encontrar período, tenta extrair todas as datas e pegar min/max
    datas = re.findall(r'\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4}', texto)
    if datas:
        try:
            datas_obj = []
            for data in datas:
                for sep in ['/', '-']:
                    if sep in data:
                        try:
                            data_obj = datetime.strptime(data, f'%d{sep}%m{sep}%Y')
                            datas_obj.append(data_obj)
                            break
                        except ValueError:
                            continue
            
            if datas_obj:
                data_min = min(datas_obj)
                data_max = max(datas_obj)
                return data_min.strftime('%d/%m/%Y'), data_max.strftime('%d/%m/%Y')
        except:
            pass
    
    return None, None


def calcular_hash_transacao(data: str, descricao: str, valor: float) -> str:
    """Calcula hash único para uma transação (para detectar duplicatas)."""
    import hashlib
    conteudo = f"{data}|{descricao.lower().strip()}|{valor}"
    return hashlib.md5(conteudo.encode()).hexdigest()


def validar_formato_monetario(valor_str: str) -> Tuple[bool, float]:
    """Valida e converte string monetária para float."""
    try:
        # Remove símbolos monetários
        valor_limpo = re.sub(r'[R$\s]', '', valor_str)
        
        # Trata diferentes formatos
        if ',' in valor_limpo and '.' in valor_limpo:
            # Formato brasileiro: 1.234,56
            if valor_limpo.rindex(',') > valor_limpo.rindex('.'):
                valor_limpo = valor_limpo.replace('.', '').replace(',', '.')
            # Formato americano: 1,234.56
            else:
                valor_limpo = valor_limpo.replace(',', '')
        elif ',' in valor_limpo:
            # Pode ser decimal brasileiro (123,45) ou separador de milhares (1,234)
            if len(valor_limpo.split(',')[-1]) == 2:
                valor_limpo = valor_limpo.replace(',', '.')
            else:
                valor_limpo = valor_limpo.replace(',', '')
        
        valor_float = float(valor_limpo)
        return True, valor_float
    except:
        return False, 0.0


def categorizar_transacao_automatica(descricao: str) -> Tuple[str, str]:
    """Categoriza transação automaticamente com base na descrição."""
    descricao_lower = descricao.lower()
    
    # Mapas de categorização
    categoria_map = {
        'alimentação': ['supermercado', 'mercado', 'padaria', 'restaurante', 'lanchonete', 'delivery', 'ifood', 'uber eats'],
        'transporte': ['uber', 'taxi', 'combustivel', 'posto', 'metro', 'onibus', 'transporte'],
        'saúde': ['farmacia', 'drogaria', 'hospital', 'clinica', 'medico', 'dentista', 'consulta'],
        'educação': ['escola', 'universidade', 'curso', 'livro', 'material escolar'],
        'lazer': ['cinema', 'teatro', 'show', 'netflix', 'spotify', 'jogo'],
        'casa': ['aluguel', 'condominio', 'energia', 'agua', 'gas', 'internet', 'telefone'],
        'vestuário': ['roupa', 'sapato', 'loja', 'shopping'],
        'investimento': ['aplicação', 'cdb', 'poupança', 'investimento', 'renda fixa'],
        'receita': ['salario', 'freelance', 'venda', 'receita', 'deposito', 'transferencia recebida']
    }
    
    for categoria, palavras_chave in categoria_map.items():
        if any(palavra in descricao_lower for palavra in palavras_chave):
            return categoria, ''
    
    return 'outros', ''


# --- LOGS E MONITORAMENTO ---

def log_extrato_processamento(user_id: int, tipo_arquivo: str, num_transacoes: int, sucesso: bool):
    """Registra log do processamento de extrato."""
    logger.info(f"Extrato processado - User: {user_id}, Tipo: {tipo_arquivo}, "
                f"Transações: {num_transacoes}, Sucesso: {sucesso}")


def obter_estatisticas_extrato(db: Session, user_id: int) -> Dict:
    """Obtém estatísticas de extratos importados pelo usuário."""
    try:
        total_importados = db.query(Lancamento).filter(
            Lancamento.id_usuario == user_id,
            Lancamento.origem == 'Extrato Importado'
        ).count()
        
        return {
            'total_importados': total_importados,
            'ultima_importacao': datetime.now().strftime('%d/%m/%Y %H:%M')
        }
    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        return {'total_importados': 0, 'ultima_importacao': 'N/A'}


# --- EXPORT DAS FUNÇÕES PRINCIPAIS ---

__all__ = [
    'criar_conversation_handler_extrato',
    'ExtratoProcessor',
    'ExtratoValidator',
    'AWAIT_EXTRATO_FILE',
    'AWAIT_CONTA_ASSOCIADA',
    'AWAIT_CONFIRMATION'
]
# --- HANDLER ATUALIZADO ---
extrato_conv = ConversationHandler(
    entry_points=[CommandHandler('extrato', extrato_start)],
    states={
        AWAIT_EXTRATO_FILE: [
            MessageHandler(filters.Document.ALL, processar_extrato_arquivo),
        ],
        AWAIT_CONTA_ASSOCIADA: [CallbackQueryHandler(associar_conta_e_confirmar_extrato, pattern='^extrato_conta_')],
        AWAIT_CONFIRMATION: [
            CallbackQueryHandler(salvar_transacoes_extrato_em_lote, pattern='^extrato_confirm_save$'),
            CallbackQueryHandler(cancel, pattern='^extrato_confirm_cancel$'),
        ]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
)
