import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

# --- CORREÇÃO: Importamos as funções do ocr_handler, mas não os estados ---
from .ocr_handler import ocr_iniciar_como_subprocesso, ocr_action_processor
from .handlers import cancel

from database.database import get_db, get_or_create_user
from models import Categoria, Subcategoria, Lancamento, Conta, Usuario
from .states import (
    AWAITING_LAUNCH_ACTION, ASK_DESCRIPTION, ASK_VALUE, ASK_CONTA,
    ASK_CATEGORY, ASK_SUBCATEGORY, ASK_DATA, OCR_CONFIRMATION_STATE
)

logger = logging.getLogger(__name__)



# --- FUNÇÃO DE MENU REUTILIZÁVEL ---
async def show_launch_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None, new_message: bool = False):
    """
    Exibe o menu principal de lançamento de forma consistente.
    Agora lida com a criação de uma nova mensagem quando necessário.
    """
    text = message_text or (
    "<b>Pronto para registrar um novo lançamento?</b> 🧾✨\n\n"
    "Você pode clicar em <b>Entrada/Saída</b> para adicionar manualmente,\n"
    "ou me enviar uma <b>foto ou PDF do seu cupom fiscal</b> 📷📄 que eu cuido do resto!\n\n"
    "Vamos deixar suas finanças organizadinhas? 💼✅"
)
    
    keyboard = [
        [
            InlineKeyboardButton("🟢 Entrada", callback_data="manual_type_Entrada"),
            InlineKeyboardButton("🔴 Saída", callback_data="manual_type_Saída")
        ],
        [InlineKeyboardButton("✅ Concluir Lançamentos", callback_data="manual_finish")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Se for forçado a enviar uma nova mensagem ou se não houver um callback_query para editar
    if new_message or not (hasattr(update, 'callback_query') and update.callback_query and update.callback_query.message):
        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=text, 
            parse_mode='HTML', 
            reply_markup=reply_markup
        )
    else: # Se houver um callback_query válido, tenta editar a mensagem
        try:
            await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        except Exception as e:
            # Fallback: se a edição falhar (ex: mensagem muito antiga), envia uma nova.
            logger.warning(f"Falha ao editar mensagem no show_launch_menu, enviando nova. Erro: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id, 
                text=text, 
                parse_mode='HTML', 
                reply_markup=reply_markup
            )


# --- PONTO DE ENTRADA E FLUXO PRINCIPAL ---
async def manual_entry_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de lançamento unificado."""
    # Limpa dados de lançamentos anteriores para começar uma nova "sessão"
    context.user_data.clear()
    
    texto_inicial = (
        "Vamos adicionar um novo lançamento. É uma <b>Entrada</b> ou uma <b>Saída</b>?\n\n"
        "✨ <i>Ou, se preferir, pode apenas me enviar a <b>foto do cupom fiscal</b> agora.</i>"
    )
    await show_launch_menu(update, context, message_text=texto_inicial)
    
    return AWAITING_LAUNCH_ACTION


# --- FLUXO MANUAL (INICIADO PELOS BOTÕES) ---
async def start_manual_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo manual após clique em 'Entrada' ou 'Saída'."""
    query = update.callback_query
    await query.answer()
    
    # Salva o tipo (Entrada/Saída) e pede a descrição
    context.user_data['novo_lancamento'] = {'tipo': query.data.split('_')[-1]}
    await query.edit_message_text("Ok. Qual a <b>descrição</b> para este lançamento? (Ex: 'Almoço no shopping')", parse_mode='HTML')
    
    return ASK_DESCRIPTION

# ... (As funções ask_description, ask_value, ask_conta, ask_category, ask_subcategory, ask_data continuam EXATAMENTE IGUAIS) ...
# ... (As copiamos aqui para manter o arquivo completo e funcional) ...
async def ask_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['novo_lancamento']['descricao'] = update.message.text
    await update.message.reply_text("Qual o valor? (Ex: 45.50)")
    return ASK_VALUE

def criar_teclado_colunas(botoes: list, colunas: int):
    if not botoes: return []
    return [botoes[i:i + colunas] for i in range(0, len(botoes), colunas)]
    
async def ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data['novo_lancamento']['valor'] = float(update.message.text.replace(',', '.'))
        db = next(get_db())
        user_db = db.query(Usuario).filter(Usuario.telegram_id == update.effective_user.id).first()
        contas = db.query(Conta).filter(Conta.id_usuario == user_db.id).all()
        db.close()

        if not contas:
            await update.message.reply_text("Você não tem nenhuma conta ou cartão cadastrado. Use /configurar para adicionar. Lançamento cancelado.")
            return await finish_flow(update, context)

        botoes = [InlineKeyboardButton(c.nome, callback_data=f"manual_conta_{c.id}") for c in contas]
        teclado = criar_teclado_colunas(botoes, 2)
        await update.message.reply_html("De qual <b>conta ou cartão</b> saiu/entrou o dinheiro?", reply_markup=InlineKeyboardMarkup(teclado))
        return ASK_CONTA
    except ValueError:
        await update.message.reply_text("⚠️ Valor inválido. Por favor, digite apenas números.")
        return ASK_VALUE

async def ask_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    conta_id = int(query.data.split('_')[-1])
    db = next(get_db())
    conta_obj = db.query(Conta).filter(Conta.id == conta_id).first()
    context.user_data['novo_lancamento']['id_conta'] = conta_id
    context.user_data['novo_lancamento']['forma_pagamento'] = conta_obj.nome
    
    categorias = db.query(Categoria).order_by(Categoria.nome).all()
    db.close()
    
    botoes = [InlineKeyboardButton(c.nome, callback_data=f"manual_cat_{c.id}") for c in categorias]
    teclado = criar_teclado_colunas(botoes, 2)
    teclado.append([InlineKeyboardButton("🏷️ Sem Categoria", callback_data="manual_cat_0")])
    await query.edit_message_text("Ótimo. Agora, em qual <b>categoria</b> se encaixa?", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='HTML')
    return ASK_CATEGORY

async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split('_')[-1])
    
    if category_id == 0:
        context.user_data['novo_lancamento']['id_categoria'] = None
        context.user_data['novo_lancamento']['id_subcategoria'] = None
        return await ask_data_entry_point(update, context)

    context.user_data['novo_lancamento']['id_categoria'] = category_id
    db = next(get_db())
    subcategorias = db.query(Subcategoria).filter(Subcategoria.id_categoria == category_id).order_by(Subcategoria.nome).all()
    db.close()

    if not subcategorias:
        context.user_data['novo_lancamento']['id_subcategoria'] = None
        return await ask_data_entry_point(update, context)

    botoes = [InlineKeyboardButton(s.nome, callback_data=f"manual_subcat_{s.id}") for s in subcategorias]
    teclado = criar_teclado_colunas(botoes, 2)
    teclado.append([InlineKeyboardButton("↩️ Sem Subcategoria", callback_data="manual_subcat_0")])
    await query.edit_message_text("E a <b>subcategoria</b>?", reply_markup=InlineKeyboardMarkup(teclado), parse_mode='HTML')
    return ASK_SUBCATEGORY

async def ask_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    subcategory_id = int(query.data.split('_')[-1])
    context.user_data['novo_lancamento']['id_subcategoria'] = subcategory_id if subcategory_id != 0 else None
    return await ask_data_entry_point(update, context)

async def ask_data_entry_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "Qual a data da transação? (DD/MM/AAAA)\n\n<i>Digite 'hoje' para usar a data atual.</i>"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode='HTML')
    else:
        await update.message.reply_html(text)
    return ASK_DATA

async def save_manual_lancamento_and_return(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva o lançamento manual e volta para o menu principal de lançamentos."""
    data_texto = update.message.text.lower()
    try:
        if data_texto == 'hoje':
            data_transacao = datetime.now()
        else:
            data_transacao = datetime.strptime(data_texto, '%d/%m/%Y')
        context.user_data['novo_lancamento']['data_transacao'] = data_transacao
    except ValueError:
        await update.message.reply_text("⚠️ Formato de data inválido. Use DD/MM/AAAA ou 'hoje'.")
        return ASK_DATA

    db = next(get_db())
    try:
        # ... (código de salvar no banco, exatamente como antes) ...
        user_info = update.effective_user
        usuario_db = get_or_create_user(db, user_info.id, user_info.full_name)
        dados = context.user_data['novo_lancamento']
        
        novo_lancamento = Lancamento(id_usuario=usuario_db.id, **dados)
        db.add(novo_lancamento)
        db.commit()
        await update.message.reply_text("✅ Lançamento manual registrado com sucesso!")
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar lançamento manual: {e}", exc_info=True)
        await update.message.reply_text("❌ Erro ao salvar o lançamento.")
    finally:
        db.close()
        context.user_data.pop('novo_lancamento', None)

    # Volta para o menu principal
    await show_launch_menu(update, context)
    return AWAITING_LAUNCH_ACTION


# --- FLUXO DE OCR (INICIADO POR ARQUIVO) ---
async def ocr_flow_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ponto de entrada para o fluxo de OCR quando um arquivo é enviado."""
    # Chama a função de processamento do OCR
    # A função ocr_iniciar_como_subprocesso agora retorna um estado
    return await ocr_iniciar_como_subprocesso(update, context)

async def ocr_confirmation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    action = query.data
    await ocr_action_processor(update, context)
    
    if action in ["ocr_salvar", "ocr_cancelar"]:
        await query.message.delete()
        msg = "✅ Lançamento por OCR salvo! O que vamos registrar agora?" if action == "ocr_salvar" else "Lançamento por OCR cancelado. O que deseja fazer?"
        await show_launch_menu(update, context, message_text=msg, new_message=True)
        return AWAITING_LAUNCH_ACTION
    
    return OCR_CONFIRMATION_STATE


# --- FUNÇÃO DE ENCERRAMENTO ---
async def finish_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Sessão de lançamentos concluída.")
    context.user_data.clear()
    return ConversationHandler.END


# --- HANDLER UNIFICADO ---
manual_entry_conv = ConversationHandler(
    entry_points=[CommandHandler('lancamento', manual_entry_start)],
    states={
        AWAITING_LAUNCH_ACTION: [
            CallbackQueryHandler(start_manual_flow, pattern='^manual_type_'),
            CallbackQueryHandler(finish_flow, pattern='^manual_finish$'),
            MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Document.MimeType("application/pdf"), ocr_iniciar_como_subprocesso),
        ],
        ASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_description)],
        ASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_value)],
        ASK_CONTA: [CallbackQueryHandler(ask_conta, pattern='^manual_conta_')],
        ASK_CATEGORY: [CallbackQueryHandler(ask_category, pattern='^manual_cat_')],
        ASK_SUBCATEGORY: [CallbackQueryHandler(ask_subcategory, pattern='^manual_subcat_')],
        ASK_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_manual_lancamento_and_return)],
        OCR_CONFIRMATION_STATE: [CallbackQueryHandler(ocr_confirmation_handler, pattern='^ocr_')]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)