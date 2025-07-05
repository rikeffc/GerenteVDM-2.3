import logging
import json
import re
from datetime import datetime, timedelta
import io

from pdf2image import convert_from_bytes
import google.generativeai as genai
from google.cloud import vision
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy.orm import Session, joinedload # Adicionando o import que faltava
from sqlalchemy import and_, func # Adicionando o import que faltava

import config
from database.database import get_or_create_user, get_db
from models import Lancamento, ItemLancamento, Categoria, Subcategoria, Usuario
from .states import OCR_CONFIRMATION_STATE

logger = logging.getLogger(__name__)

PROMPT_IA_OCR = """
**TAREFA:** Você é uma API especialista em analisar notas fiscais e comprovantes brasileiros para extrair e classificar os dados em um objeto JSON.
**REGRAS CRÍTICAS:**
- **SEMPRE** retorne um único objeto JSON válido, sem nenhum texto antes ou depois.
- Se um campo não for encontrado, retorne `null`.
**CONTEXTO DE CATEGORIAS DISPONÍVEIS:**
Use **EXATAMENTE** uma das seguintes categorias e suas respectivas subcategorias para classificar a transação.
{categorias_disponiveis}
**REGRAS DE EXTRAÇÃO:**
1. `documento_fiscal`: CNPJ/CPF do estabelecimento (apenas números).
2. `nome_estabelecimento`: Nome da loja/empresa. Para PIX, o nome do pagador. Para maquininhas (Cielo, Rede), use "Compra no Cartão".
3. `valor_total`: Valor final da transação.
4. `data` e `hora`: Data (dd/mm/yyyy) e hora (HH:MM:SS) da transação.
5. `forma_pagamento`: PIX, Crédito, Débito, Dinheiro, etc.
6. `tipo_transacao`: "Entrada" para recebimentos, "Saída" para compras.
7. `itens`: Uma lista de objetos com `nome_item`, `quantidade`, `valor_unitario`. Para comprovantes sem itens detalhados, retorne `[]`.
8. `categoria_sugerida`: Com base nos itens e no estabelecimento, escolha a MELHOR categoria da lista fornecida.
9. `subcategoria_sugerida`: Após escolher a categoria, escolha a MELHOR subcategoria correspondente da lista.
**EXEMPLO DE SAÍDA PERFEITA (FARMÁCIA):**
```json
{{
    "documento_fiscal": "12345678000199",
    "nome_estabelecimento": "DROGARIA PACHECO",
    "valor_total": 55.80,
    "data": "28/06/2025",
    "hora": "15:30:00",
    "forma_pagamento": "Crédito",
    "tipo_transacao": "Saída",
    "itens": [
        {{"nome_item": "DORFLEX", "quantidade": 1, "valor_unitario": 25.50}},
        {{"nome_item": "VITAMINA C", "quantidade": 1, "valor_unitario": 30.30}}
    ],
    "categoria_sugerida": "Saúde",
    "subcategoria_sugerida": "Farmácia"
}}
TEXTO EXTRAÍDO DO OCR PARA ANÁLISE:
{texto_ocr}
"""

async def _reply_with_summary(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    """
    Gera e envia o resumo da transação lida pelo OCR. (Função sem alterações)
    """
    dados_ia = context.user_data.get('dados_ocr')
    if not dados_ia:
        return
    # ... (O código desta função permanece exatamente o mesmo que o seu original) ...
    tipo_atual = dados_ia.get('tipo_transacao', 'Saída')
    tipo_emoji = "🔴" if tipo_atual == 'Saída' else "🟢"
    novo_tipo_texto = "Marcar como Entrada" if tipo_atual == 'Saída' else "Marcar como Saída"
    doc = dados_ia.get('documento_fiscal') or "N/A"
    tipo_doc = "CNPJ" if len(str(doc)) == 14 else "CPF"
    categoria_sugerida = dados_ia.get('categoria_sugerida', 'N/A')
    subcategoria_sugerida = dados_ia.get('subcategoria_sugerida', 'N/A')
    categoria_str = f"{categoria_sugerida} / {subcategoria_sugerida}" if subcategoria_sugerida != 'N/A' else categoria_sugerida
    valor_float = float(dados_ia.get('valor_total', 0.0))

    itens_str = ""
    itens_lista = dados_ia.get('itens', [])
    if itens_lista:
        itens_formatados = []
        for item in itens_lista:
            nome = item.get('nome_item', 'N/A')
            qtd = item.get('quantidade', 1)
            val_unit = float(item.get('valor_unitario', 0.0))
            itens_formatados.append(f"  • {qtd}x {nome} - <code>R$ {val_unit:.2f}</code>")
        itens_str = "\n🛒 <b>Itens Comprados:</b>\n" + "\n".join(itens_formatados)

    msg = (
        f"🧾 <b>Resumo da Transação</b>\n\n"
        f"🏢 <b>Estabelecimento:</b> {dados_ia.get('nome_estabelecimento', 'N/A')}\n"
        f"🆔 <b>{tipo_doc}:</b> {doc}\n"
        f"📂 <b>Categoria Sugerida:</b> {categoria_str}\n"
        f"📅 <b>Data:</b> {dados_ia.get('data', 'N/A')} 🕒 <b>Hora:</b> {dados_ia.get('hora', 'N/A')}\n"
        f"💳 <b>Pagamento:</b> {dados_ia.get('forma_pagamento', 'N/A')}"
        f"{itens_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Tipo:</b> {tipo_atual} {tipo_emoji}\n"
        f"💰 <b>Valor Total:</b> <code>R$ {valor_float:.2f}</code>\n\n"
        f"✅ <b>Está tudo correto?</b>"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Confirmar e Salvar", callback_data="ocr_salvar")],
        [InlineKeyboardButton(f"🔄 {novo_tipo_texto}", callback_data="ocr_toggle_type")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="ocr_cancelar")]
    ]

    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(msg, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update_or_query.message.reply_html(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def ocr_iniciar_como_subprocesso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Processa um arquivo (foto ou pdf) e retorna um estado de confirmação.
    """
    message = await update.message.reply_text("📸 Arquivo capturado! Começando a leitura...🤖📄")
    try:
        is_photo = bool(update.message.photo)
        file_source = update.message.photo[-1] if is_photo else update.message.document

        await message.edit_text("📥 Baixando arquivo do Telegram...")
        telegram_file = await file_source.get_file()
        file_bytearray = await telegram_file.download_as_bytearray()
        file_bytes = bytes(file_bytearray)

        image_content_for_vision = None

        # Lógica de processamento de PDF e Imagem (sem alterações)
        if not is_photo and file_source.mime_type == 'application/pdf':
            await message.edit_text("📄 PDF detectado! Convertendo para imagem...")
            images = convert_from_bytes(file_bytes, first_page=1, last_page=1, fmt='png')
            if not images:
                await message.edit_text("❌ Não foi possível converter o PDF para imagem.")
                return ConversationHandler.END
            with io.BytesIO() as output:
                images[0].save(output, format="PNG")
                image_content_for_vision = output.getvalue()
        else:
            image_content_for_vision = file_bytes

        if not image_content_for_vision:
            await message.edit_text("❌ Não foi possível processar o arquivo enviado.")
            return ConversationHandler.END

        await message.edit_text("🔎 Lendo conteúdo com Google Vision...")
        vision_image = vision.Image(content=image_content_for_vision)
        vision_client = vision.ImageAnnotatorClient()
        response = vision_client.document_text_detection(image=vision_image)
        texto_ocr = response.full_text_annotation.text

        if not texto_ocr or len(texto_ocr.strip()) < 20:
            await message.edit_text(
                "⚠️ Não consegui extrair dados claros desta imagem.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END

        await message.edit_text("📚 Buscando categorias para análise...")
        db: Session = next(get_db())
        try:
            categorias_db = db.query(Categoria).options(joinedload(Categoria.subcategorias)).all()
            categorias_formatadas = [
                f"- {cat.nome}: ({', '.join(sub.nome for sub in cat.subcategorias)})" for cat in categorias_db
            ]
            categorias_contexto = "\n".join(categorias_formatadas)
        finally:
            db.close()

        await message.edit_text("🧠 Texto extraído! Analisando com a IA...")
        model = genai.GenerativeModel(config.GEMINI_MODEL_NAME)
        prompt = PROMPT_IA_OCR.format(texto_ocr=texto_ocr, categorias_disponiveis=categorias_contexto)
        ia_response = await model.generate_content_async(prompt)

        response_text = ia_response.text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            logger.error(f"Nenhum JSON válido foi encontrado na resposta da IA: {response_text}")
            await message.edit_text("❌ A IA retornou um formato inesperado. Tente novamente.")
            return ConversationHandler.END

        json_str = json_match.group(0)

        try:
            dados_ia = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar JSON da IA: {e}\nString Tentada: {json_str}")
            await message.edit_text("❌ A IA retornou um formato inválido. Tente novamente.")
            return ConversationHandler.END

        valor_bruto = dados_ia.get('valor_total')
        valor_str = str(valor_bruto or '0').replace(',', '.')
        dados_ia['valor_total'] = float(valor_str) if valor_str else 0.0

        context.user_data['dados_ocr'] = dados_ia

        await message.delete()
        await _reply_with_summary(update, context)

        # AQUI ESTÁ A MUDANÇA CRUCIAL
        return OCR_CONFIRMATION_STATE

    except Exception as e:
        logger.error(f"Erro CRÍTICO no fluxo de OCR (ocr_iniciar_como_subprocesso): {e}", exc_info=True)
        try:
            await message.edit_text("❌ Ops! Ocorreu um erro inesperado. O erro foi registrado.")
        except Exception as inner_e:
            logger.error(f"Não foi possível editar a mensagem de erro: {inner_e}")
        return ConversationHandler.END

async def ocr_action_processor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Processa a ação do botão de confirmação do OCR.
    Esta função não retorna um estado, apenas realiza a ação (salvar, etc.).
    """
    query = update.callback_query
    action = query.data
    dados = context.user_data.get('dados_ocr')
    if not dados and action != 'ocr_cancelar':
        await query.answer("Erro: Dados da sessão perdidos.", show_alert=True)
        return

    if action == "ocr_toggle_type":
        dados['tipo_transacao'] = 'Entrada' if dados.get('tipo_transacao') == 'Saída' else 'Saída'
        context.user_data['dados_ocr'] = dados
        await _reply_with_summary(query, context)
        return  # Permanece no mesmo estado, apenas atualiza a mensagem

    if action == "ocr_salvar":
        await query.edit_message_text("💾 Verificando e salvando no banco de dados...")
        db: Session = next(get_db())
        try:
            # Lógica de verificação de duplicidade e salvamento (sem alterações)
            user_info = query.from_user
            usuario_db = get_or_create_user(db, user_info.id, user_info.full_name)
            data_str = dados.get('data', datetime.now().strftime('%d/%m/%Y'))
            hora_str = dados.get('hora', '00:00:00')
            try:
                data_obj = datetime.strptime(f"{data_str} {hora_str}", '%d/%m/%Y %H:%M:%S')
            except ValueError:
                data_obj = datetime.strptime(data_str, '%d/%m/%Y')
            doc_fiscal = re.sub(r'\D', '', str(dados.get('documento_fiscal', ''))) or None
            time_window_start = data_obj - timedelta(minutes=5)
            time_window_end = data_obj + timedelta(minutes=5)
            existing_lancamento = db.query(Lancamento).filter(
                and_(
                    Lancamento.id_usuario == usuario_db.id,
                    Lancamento.valor == dados.get('valor_total'),
                    Lancamento.documento_fiscal == doc_fiscal,
                    Lancamento.data_transacao.between(time_window_start, time_window_end)
                )
            ).first()
            if existing_lancamento:
                await query.edit_message_text("⚠️ Transação Duplicada! Operação cancelada.", parse_mode='Markdown')
                return

            # Lógica de encontrar categoria/subcategoria (sem alterações)
            id_categoria, id_subcategoria = None, None
            if cat_sugerida := dados.get('categoria_sugerida'):
                categoria_obj = db.query(Categoria).filter(func.lower(Categoria.nome) == func.lower(cat_sugerida)).first()
                if categoria_obj:
                    id_categoria = categoria_obj.id
            if sub_sugerida := dados.get('subcategoria_sugerida'):
                if id_categoria:
                    subcategoria_obj = db.query(Subcategoria).filter(and_(Subcategoria.id_categoria == id_categoria, func.lower(Subcategoria.nome) == func.lower(sub_sugerida))).first()
                    if subcategoria_obj:
                        id_subcategoria = subcategoria_obj.id

            # Criação do lançamento e itens (sem alterações)
            novo_lancamento = Lancamento(
                id_usuario=usuario_db.id,
                data_transacao=data_obj,
                descricao=dados.get('nome_estabelecimento'),
                valor=dados.get('valor_total'),
                tipo=dados.get('tipo_transacao', 'Saída'),
                forma_pagamento=dados.get('forma_pagamento'),
                documento_fiscal=doc_fiscal,
                id_categoria=id_categoria,
                id_subcategoria=id_subcategoria
            )
            for item_data in dados.get('itens', []):
                valor_unit_str = str(item_data.get('valor_unitario', '0')).replace(',', '.')
                valor_unit = float(valor_unit_str) if valor_unit_str else 0.0
                qtd_str = str(item_data.get('quantidade', '1')).replace(',', '.')
                qtd = float(qtd_str) if qtd_str else 1.0
                novo_item = ItemLancamento(
                    nome_item=item_data.get('nome_item', 'Item desconhecido'),
                    quantidade=qtd,
                    valor_unitario=valor_unit
                )
                novo_lancamento.itens.append(novo_item)

            db.add(novo_lancamento)
            db.commit()

            # Mensagem de sucesso será enviada pelo handler principal
        except Exception as e:
            db.rollback()
            logger.error(f"Erro ao salvar no banco (ocr_action_handler): {e}", exc_info=True)
            await query.edit_message_text("❌ Falha ao salvar no banco de dados. O erro foi registrado.")
        finally:
            db.close()
            context.user_data.pop('dados_ocr', None)