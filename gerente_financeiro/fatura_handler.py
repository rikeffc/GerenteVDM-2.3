import logging
import json
import re
from datetime import datetime, timedelta
import io

from PyPDF2 import PdfReader
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)
from sqlalchemy.orm import Session
from sqlalchemy import and_, extract

import config
from database.database import get_db, get_or_create_user
from models import Lancamento, Categoria, Subcategoria, Conta, Usuario
from .handlers import cancel  # Reutilizando a função de cancelamento

logger = logging.getLogger(__name__)

# --- ESTADOS DA CONVERSA ---
(
    AWAIT_FATURA_PDF,
    AWAIT_CONTA_ASSOCIADA,
    AWAIT_CONFIRMATION
) = range(800, 803)

# --- PROMPT PARA A IA ---
PROMPT_ANALISE_FATURA = """
**TAREFA:** Você é uma API especialista em analisar o texto de faturas de cartão de crédito brasileiras. Sua única função é extrair as transações e classificá-las em um objeto JSON.

**REGRAS CRÍTICAS:**
- **SEMPRE** retorne um único objeto JSON válido, sem nenhum texto antes ou depois.
- Se um campo não for encontrado, retorne `null` ou um valor padrão (lista vazia para `transacoes`).
- **IGNORE** lançamentos de "PAGAMENTO RECEBIDO", "SALDO ANTERIOR", "CREDITO ROTATIVO", juros, encargos, "IOF" e qualquer coisa que não seja uma compra real do usuário.
- Para a data, se o ano não estiver explícito, assuma o ano atual: {ano_atual}.

**CONTEXTO DE CATEGORIAS E SUBCATEGORIAS DISPONÍVEIS:**
Use **EXATAMENTE** uma das seguintes categorias e suas respectivas subcategorias para classificar cada transação. Seja o mais preciso possível.
{categorias_disponiveis}

**FORMATO DA SAÍDA JSON:**
```json
{{
  "nome_cartao_sugerido": "Nome do Cartão (ex: NUBANK, INTER GOLD)",
  "vencimento_fatura_sugerido": "DD/MM/AAAA",
  "transacoes": [
    {{
      "data": "DD/MM/AAAA",
      "descricao": "NOME DO ESTABELECIMENTO OU COMPRA",
      "valor": VALOR_NUMERICO_FLOAT,
      "categoria_sugerida": "Nome Exato da Categoria da Lista",
      "subcategoria_sugerida": "Nome Exato da Subcategoria da Lista"
    }}
  ]
}}
EXEMPLO DE SAÍDA PERFEITA:
{{
  "nome_cartao_sugerido": "NUBANK MASTERCARD",
  "vencimento_fatura_sugerido": "15/07/2025",
  "transacoes": [
    {{"data": "20/06/2025", "descricao": "UBER TRIP", "valor": 25.50, "categoria_sugerida": "Transporte", "subcategoria_sugerida": "App de Transporte"}},
    {{"data": "22/06/2025", "descricao": "IFOOD*RESTAURANTE", "valor": 55.90, "categoria_sugerida": "Alimentação", "subcategoria_sugerida": "Restaurante/Delivery"}},
    {{"data": "23/06/2025", "descricao": "NETFLIX.COM", "valor": 39.90, "categoria_sugerida": "Lazer", "subcategoria_sugerida": "Cinema/Streaming"}}
  ]
}}
TEXTO EXTRAÍDO DA FATURA PARA ANÁLISE:
{texto_fatura}
"""

# --- FUNÇÕES DO FLUXO ---

async def fatura_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Inicia o fluxo de análise de fatura.
    """
    # (Futuramente, aqui você verificaria se o usuário é Premium)
    await update.message.reply_html(
        "📄 <b>Analisador de Faturas de Cartão</b>\n\n"
        "Envie o arquivo PDF da sua fatura e eu vou extrair e categorizar todas as transações para você! ✨"
    )
    return AWAIT_FATURA_PDF


async def processar_fatura_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Recebe o PDF, extrai o texto e envia para a IA.
    """
    message = await update.message.reply_text("📥 Fatura recebida! Processando o arquivo PDF...")
    try:
        file_source = update.message.document
        if file_source.mime_type != 'application/pdf':
            await message.edit_text("❌ Por favor, envie um arquivo no formato PDF.")
            return AWAIT_FATURA_PDF

        telegram_file = await file_source.get_file()
        file_bytearray = await telegram_file.download_as_bytearray()

        # Extrair texto do PDF
        await message.edit_text("🔎 Extraindo texto da fatura...")
        pdf_reader = PdfReader(io.BytesIO(file_bytearray))
        texto_fatura = ""
        for page in pdf_reader.pages:
            texto_fatura += page.extract_text()

        if not texto_fatura or len(texto_fatura.strip()) < 50:
            await message.edit_text("⚠️ Não consegui extrair texto claro desta fatura. O PDF pode ser uma imagem.")
            return ConversationHandler.END

        # Buscar categorias para o prompt da IA
        await message.edit_text("📚 Buscando categorias para análise...")
        db: Session = next(get_db())
        try:
            categorias_db = db.query(Categoria).all()
            categorias_formatadas = [
                f"- {cat.nome}: ({', '.join(sub.nome for sub in cat.subcategorias)})" for cat in categorias_db
            ]
            categorias_contexto = "\n".join(categorias_formatadas)
        finally:
            db.close()

        # Chamar a IA para análise
        await message.edit_text("🧠 Enviando para análise da IA... Isso pode levar um momento.")
        model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)
        prompt = PROMPT_ANALISE_FATURA.format(
            texto_fatura=texto_fatura,
            categorias_disponiveis=categorias_contexto,
            ano_atual=datetime.now().year
        )
        ia_response = await model.generate_content_async(prompt)

        # Limpar e decodificar a resposta JSON
        response_text = ia_response.text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            logger.error(f"Nenhum JSON encontrado na resposta da IA para fatura: {response_text}")
            await message.edit_text("❌ A IA não conseguiu analisar a fatura. Tente um arquivo diferente.")
            return ConversationHandler.END

        try:
            dados_fatura = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.error(f"Erro de JSONDecode na resposta da IA para fatura: {json_match.group(0)}")
            await message.edit_text("❌ A IA retornou um formato inválido. Tente novamente.")
            return ConversationHandler.END

        if not dados_fatura.get('transacoes'):
            await message.edit_text("🤔 A IA não encontrou nenhuma transação de compra nesta fatura.")
            return ConversationHandler.END

        context.user_data['dados_fatura'] = dados_fatura

        # Perguntar a qual conta associar
        db = next(get_db())
        try:
            user_db = get_or_create_user(db, update.effective_user.id, update.effective_user.full_name)
            cartoes = db.query(Conta).filter(
                Conta.id_usuario == user_db.id,
                Conta.tipo == 'Cartão de Crédito'
            ).all()

            if not cartoes:
                await message.edit_text(
                    "Você não tem nenhum cartão de crédito cadastrado. Use `/configurar` para adicionar um e tente novamente."
                )
                return ConversationHandler.END

            botoes = [[InlineKeyboardButton(c.nome, callback_data=f"fatura_conta_{c.id}")] for c in cartoes]
            await message.edit_text(
                f"💳 Análise concluída! Encontrei <b>{len(dados_fatura['transacoes'])}</b> transações.\n\n"
                "A qual dos seus cartões cadastrados esta fatura pertence?",
                reply_markup=InlineKeyboardMarkup(botoes),
                parse_mode='HTML'
            )
            return AWAIT_CONTA_ASSOCIADA
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Erro CRÍTICO no processamento da fatura: {e}", exc_info=True)
        await message.edit_text("❌ Ops! Ocorreu um erro inesperado ao processar sua fatura.")
        return ConversationHandler.END


async def associar_conta_e_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Associa a conta e mostra o resumo COMPLETO para confirmação."""
    query = update.callback_query
    await query.answer()
    
    conta_id = int(query.data.split('_')[-1])
    context.user_data['conta_id_fatura'] = conta_id

    dados_fatura = context.user_data['dados_fatura']
    total_valor = sum(t['valor'] for t in dados_fatura['transacoes'])
    
    # --- NOVA LÓGICA PARA LISTA COMPLETA ---
    lista_transacoes = []
    for t in dados_fatura['transacoes']:
        data_str = t.get('data', 'N/D')
        desc = t.get('descricao', 'N/A')
        valor = t.get('valor', 0.0)
        cat = t.get('categoria_sugerida', 'N/A')
        lista_transacoes.append(f"<code>{data_str}</code> | {desc[:20]:<20} | <b>R$ {valor:>7.2f}</b> | <i>{cat}</i>")
    
    # Usa a função de enviar em blocos para não estourar o limite do Telegram
    from .handlers import enviar_texto_em_blocos # Importe a função

    texto_lista = "\n".join(lista_transacoes)
    texto_cabecalho = "<b>Confirme para Salvar</b>\n\nRevisão das transações encontradas:\n\n"
    
    # Deleta a mensagem "A qual cartão pertence?"
    await query.message.delete()
    
    # Envia a lista completa (em blocos, se necessário)
    await enviar_texto_em_blocos(context.bot, update.effective_chat.id, texto_cabecalho + texto_lista)

    # Envia a mensagem final de confirmação com o total e os botões
    texto_confirmacao = (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Total da Fatura: <b>R$ {total_valor:.2f}</b>\n"
        f"Transações a serem importadas: <b>{len(dados_fatura['transacoes'])}</b>\n\n"
        "Deseja salvar todos esses lançamentos?"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Sim, salvar tudo", callback_data="fatura_confirm_save")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="fatura_confirm_cancel")]
    ]
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=texto_confirmacao,
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return AWAIT_CONFIRMATION


async def salvar_transacoes_em_lote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Salva todas as transações extraídas no banco de dados, com verificação de duplicidade.
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💾 Verificando e salvando no banco de dados...")
    
    dados_fatura = context.user_data.get('dados_fatura')
    conta_id = context.user_data.get('conta_id_fatura')

    if not dados_fatura or not conta_id:
        await query.edit_message_text("❌ Erro: Dados da sessão perdidos. Operação cancelada.")
        return ConversationHandler.END

    db: Session = next(get_db())
    try:
        user_info = query.from_user
        usuario_db = get_or_create_user(db, user_info.id, user_info.full_name)
        
        # --- LÓGICA DE VERIFICAÇÃO DE DUPLICIDADE ---
        vencimento_str = dados_fatura.get("vencimento_fatura_sugerido")
        if vencimento_str:
            try:
                vencimento_data = datetime.strptime(vencimento_str, '%d/%m/%Y')
                # As transações de uma fatura geralmente ocorrem no mês anterior ao vencimento
                data_referencia = vencimento_data - timedelta(days=15)
                mes_fatura = data_referencia.month
                ano_fatura = data_referencia.year

                lancamentos_existentes = db.query(Lancamento).filter(
                    and_(
                        Lancamento.id_usuario == usuario_db.id,
                        Lancamento.id_conta == conta_id,
                        extract('month', Lancamento.data_transacao) == mes_fatura,
                        extract('year', Lancamento.data_transacao) == ano_fatura
                    )
                ).count()

                # Se já existem mais de 5 lançamentos, é muito provável que a fatura já foi importada
                if lancamentos_existentes > 5:
                    await query.edit_message_text(
                        f"⚠️ <b>Fatura Duplicada!</b>\n\n"
                        f"Parece que você já importou os lançamentos para o mês de referência <b>{data_referencia.strftime('%B de %Y')}</b> neste cartão.\n\n"
                        "Operação cancelada para evitar duplicidade.",
                        parse_mode='HTML'
                    )
                    return ConversationHandler.END
            except (ValueError, TypeError) as e:
                logger.warning(f"Data de vencimento da fatura em formato inválido: {vencimento_str}. Erro: {e}. Pulando verificação de duplicidade.")
        
        # --- LÓGICA DE SALVAMENTO ---
        conta_selecionada = db.query(Conta).filter(Conta.id == conta_id).one()
        categorias_map = {cat.nome.lower(): cat.id for cat in db.query(Categoria).all()}
        subcategorias_map = {(sub.id_categoria, sub.nome.lower()): sub.id for sub in db.query(Subcategoria).all()}

        novos_lancamentos = []
        for transacao in dados_fatura.get('transacoes', []):
            try:
                data_obj = datetime.strptime(transacao['data'], '%d/%m/%Y')
            except (ValueError, TypeError):
                logger.warning(f"Data de transação inválida na fatura: {transacao.get('data')}. Usando data atual.")
                data_obj = datetime.now()

            cat_nome_lower = transacao.get('categoria_sugerida', '').lower()
            id_categoria = categorias_map.get(cat_nome_lower)
            
            id_subcategoria = None
            if id_categoria:
                sub_nome_lower = transacao.get('subcategoria_sugerida', '').lower()
                id_subcategoria = subcategorias_map.get((id_categoria, sub_nome_lower))

            novo_lancamento = Lancamento(
                id_usuario=usuario_db.id,
                descricao=transacao.get('descricao', 'Lançamento de fatura'),
                valor=float(transacao.get('valor', 0.0)),
                tipo='Saída',
                data_transacao=data_obj,
                forma_pagamento=conta_selecionada.nome,
                id_conta=conta_id,
                id_categoria=id_categoria,
                id_subcategoria=id_subcategoria
            )
            novos_lancamentos.append(novo_lancamento)

        if novos_lancamentos:
            db.add_all(novos_lancamentos)
            db.commit()
            await query.edit_message_text(
                f"✅ Sucesso! <b>{len(novos_lancamentos)}</b> transações foram importadas da sua fatura.",
                parse_mode='HTML'
            )
        else:
            await query.edit_message_text("🤔 Nenhuma transação válida foi encontrada para salvar.")

    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar transações em lote: {e}", exc_info=True)
        await query.edit_message_text("❌ Ocorreu um erro grave ao tentar salvar as transações.")
    finally:
        db.close()
        context.user_data.clear()

    return ConversationHandler.END

# --- CRIAÇÃO DO HANDLER ---

fatura_conv = ConversationHandler( 
    entry_points=[CommandHandler('fatura', fatura_start)],
    states={
        AWAIT_FATURA_PDF: [MessageHandler(filters.Document.PDF, processar_fatura_pdf)],
        AWAIT_CONTA_ASSOCIADA: [CallbackQueryHandler(associar_conta_e_confirmar, pattern=r'^fatura_conta_')],
        AWAIT_CONFIRMATION: [
            CallbackQueryHandler(salvar_transacoes_em_lote, pattern='^fatura_confirm_save$'),
            CallbackQueryHandler(cancel, pattern='^fatura_confirm_cancel$')
        ]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
   
    per_message=False,
)