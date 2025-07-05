import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters
)

from database.database import get_db, get_or_create_user
from models import Categoria, Agendamento, Usuario
from .handlers import cancel, criar_teclado_colunas

logger = logging.getLogger(__name__)

# ESTADOS DA CONVERSA
(
    ASK_TIPO, ASK_DESCRICAO, ASK_VALOR, ASK_CATEGORIA, ASK_PRIMEIRO_EVENTO,
    ASK_FREQUENCIA, ASK_TIPO_RECORRENCIA, ASK_TOTAL_PARCELAS, CONFIRM_AGENDAMENTO
) = range(200, 209)

# --- INÍCIO E MENU ---
async def agendamento_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [InlineKeyboardButton("➕ Novo Agendamento", callback_data="agendamento_novo")],
        [InlineKeyboardButton("📋 Meus Agendamentos", callback_data="agendamento_listar")],
        [InlineKeyboardButton("❌ Fechar", callback_data="agendamento_fechar")],
    ]
    await update.message.reply_html(
        "🗓️ <b>Gerenciador de Agendamentos</b>\n\n"
        "Use esta função para agendar contas fixas (aluguel, salário) ou parcelamentos (compras, empréstimos).",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def agendamento_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "agendamento_fechar":
        await query.edit_message_text("✅ Gerenciador de agendamentos fechado.")
        return ConversationHandler.END
    if action == "agendamento_listar":
        await listar_agendamentos(update, context)
        return ConversationHandler.END

    if action == "agendamento_novo":
        context.user_data['novo_agendamento'] = {}
        keyboard = [
            [InlineKeyboardButton("🟢 Entrada (Recebimento)", callback_data="ag_tipo_Entrada")],
            [InlineKeyboardButton("🔴 Saída (Pagamento)", callback_data="ag_tipo_Saída")],
        ]
        await query.edit_message_text(
            "Vamos criar um novo agendamento.\n\n"
            "Primeiro, esta é uma <b>Entrada</b> ou uma <b>Saída</b>?",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
        return ASK_TIPO

# --- FLUXO DE CRIAÇÃO ---
async def ask_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['novo_agendamento']['tipo'] = query.data.split('_')[-1]
    await query.edit_message_text("📝 Qual a <b>descrição</b> deste agendamento?\n<i>(ex: Aluguel, Parcela do Carro)</i>", parse_mode='HTML')
    return ASK_DESCRICAO

async def ask_descricao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['novo_agendamento']['descricao'] = update.message.text
    await update.message.reply_html("💰 Qual o <b>valor</b> de cada lançamento/parcela?\n<i>(Se for uma compra parcelada, informe o valor da parcela. Ex: 150.50)</i>")
    return ASK_VALOR

async def ask_valor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        valor = float(update.message.text.replace(',', '.'))
        context.user_data['novo_agendamento']['valor'] = valor
        db = next(get_db())
        categorias = db.query(Categoria).order_by(Categoria.nome).all()
        db.close()
        botoes = [InlineKeyboardButton(c.nome, callback_data=f"ag_cat_{c.id}") for c in categorias]
        teclado = criar_teclado_colunas(botoes, 2)
        teclado.append([InlineKeyboardButton("🏷️ Sem Categoria", callback_data="ag_cat_0")])
        await update.message.reply_html("📂 Selecione a <b>categoria</b>:", reply_markup=InlineKeyboardMarkup(teclado))
        return ASK_CATEGORIA
    except ValueError:
        await update.message.reply_text("⚠️ Valor inválido. Por favor, digite apenas números.")
        return ASK_VALOR

async def ask_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split('_')[-1])
    context.user_data['novo_agendamento']['id_categoria'] = category_id if category_id != 0 else None
    await query.edit_message_text("🗓️ Quando será a <b>primeira ocorrência</b>?\n<i>(Use o formato DD/MM/AAAA)</i>", parse_mode='HTML')
    return ASK_PRIMEIRO_EVENTO

async def ask_primeiro_evento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        data_primeiro = datetime.strptime(update.message.text, '%d/%m/%Y').date()
        if data_primeiro < datetime.now().date():
            await update.message.reply_text("⚠️ A data não pode ser no passado. Por favor, insira uma data futura.")
            return ASK_PRIMEIRO_EVENTO
        context.user_data['novo_agendamento']['data_primeiro_evento'] = data_primeiro
        keyboard = [
            [InlineKeyboardButton("🗓️ Mensalmente", callback_data="ag_freq_mensal")],
            [InlineKeyboardButton("📅 Semanalmente", callback_data="ag_freq_semanal")],
            [InlineKeyboardButton("🔁 Apenas uma vez", callback_data="ag_freq_unico")],
        ]
        await update.message.reply_html("🔁 Com que <b>frequência</b> isso vai se repetir?", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_FREQUENCIA
    except ValueError:
        await update.message.reply_text("⚠️ Formato de data inválido. Use DD/MM/AAAA.")
        return ASK_PRIMEIRO_EVENTO

async def ask_frequencia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    frequencia = query.data.split('_')[-1]
    context.user_data['novo_agendamento']['frequencia'] = frequencia

    if frequencia == 'unico':
        context.user_data['novo_agendamento']['total_parcelas'] = 1
        return await show_agendamento_confirmation(update, context)

    keyboard = [
        [InlineKeyboardButton("🔢 Nº Fixo de Parcelas", callback_data="ag_rec_fixo")],
        [InlineKeyboardButton("♾️ Contínuo (Sem Fim)", callback_data="ag_rec_continuo")],
    ]
    await query.edit_message_text(
        "Este agendamento tem um <b>número fixo de parcelas</b> ou é <b>contínuo</b> (como uma assinatura)?",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
    )
    return ASK_TIPO_RECORRENCIA

async def ask_tipo_recorrencia(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tipo_recorrencia = query.data.split('_')[-1]

    if tipo_recorrencia == 'continuo':
        context.user_data['novo_agendamento']['total_parcelas'] = None
        return await show_agendamento_confirmation(update, context)
    
    await query.edit_message_text("🔢 Quantas <b>parcelas</b> serão no total?", parse_mode='HTML')
    return ASK_TOTAL_PARCELAS

async def ask_total_parcelas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        total_parcelas = int(update.message.text)
        if total_parcelas <= 0: raise ValueError
        context.user_data['novo_agendamento']['total_parcelas'] = total_parcelas
        return await show_agendamento_confirmation(update, context)
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Por favor, insira um número inteiro e positivo.")
        return ASK_TOTAL_PARCELAS

async def show_agendamento_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    data = context.user_data['novo_agendamento']
    tipo_str = f"<b>{data['tipo']}</b> {'🟢' if data['tipo'] == 'Entrada' else '🔴'}"
    freq_str = data['frequencia'].capitalize()
    valor_str = f"<b>Valor:</b> R$ {data['valor']:.2f}"

    if data.get('total_parcelas') and data['total_parcelas'] > 1:
        freq_str += f", em {data['total_parcelas']}x"
    elif not data.get('total_parcelas'):
        freq_str += ", contínuo"

    summary = (
        f"✅ <b>Confirme seu agendamento:</b>\n\n"
        f"<b>Ação:</b> {tipo_str}\n"
        f"<b>Descrição:</b> {data['descricao']}\n"
        f"{valor_str}\n"
        f"<b>Frequência:</b> {freq_str}\n"
        f"<b>Primeira Ocorrência:</b> {data['data_primeiro_evento'].strftime('%d/%m/%Y')}\n\n"
        "<i>Lembretes serão enviados um dia antes e no dia de cada evento.</i>"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Salvar Agendamento", callback_data="ag_confirm_save")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="ag_confirm_cancel")],
    ]
    
    target_message = update.callback_query.message if update.callback_query else update.message
    await target_message.reply_html(summary, reply_markup=InlineKeyboardMarkup(keyboard))
        
    return CONFIRM_AGENDAMENTO

async def save_agendamento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("💾 Salvando agendamento...")

    db = next(get_db())
    try:
        user_info = query.from_user
        usuario_db = get_or_create_user(db, user_info.id, user_info.full_name)
        data = context.user_data['novo_agendamento']

        novo_agendamento = Agendamento(
            id_usuario=usuario_db.id,
            descricao=data['descricao'],
            valor=data['valor'],
            tipo=data['tipo'],
            id_categoria=data.get('id_categoria'),
            data_primeiro_evento=data['data_primeiro_evento'],
            proxima_data_execucao=data['data_primeiro_evento'],
            frequencia=data['frequencia'],
            total_parcelas=data.get('total_parcelas'),
            parcela_atual=0,
            ativo=True
        )
        db.add(novo_agendamento)
        db.commit()
        await query.edit_message_text("✅ Agendamento criado com sucesso!")
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao salvar agendamento: {e}", exc_info=True)
        await query.edit_message_text("❌ Erro ao salvar o agendamento.")
    finally:
        db.close()
        context.user_data.clear()
    return ConversationHandler.END

async def listar_agendamentos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    db = next(get_db())
    usuario_db = get_or_create_user(db, user_id, "")
    agendamentos = db.query(Agendamento).filter(Agendamento.id_usuario == usuario_db.id, Agendamento.ativo == True).order_by(Agendamento.proxima_data_execucao.asc()).all()
    db.close()

    if not agendamentos:
        await query.edit_message_text("Você não tem nenhum agendamento ativo.")
        return

    await query.edit_message_text("📋 <b>Seus Agendamentos Ativos:</b>", parse_mode='HTML')
    for ag in agendamentos:
        tipo_emoji = '🟢' if ag.tipo == 'Entrada' else '🔴'
        
        if ag.total_parcelas:
            status_str = f"Parcela {ag.parcela_atual + 1} de {ag.total_parcelas}"
        else:
            status_str = "Contínuo"

        mensagem = (
            f"--- \n"
            f"{tipo_emoji} <b>{ag.descricao}</b>\n"
            f"💰 Valor: R$ {ag.valor:.2f}\n"
            f"🗓️ Próximo: {ag.proxima_data_execucao.strftime('%d/%m/%Y')}\n"
            f"🔄 Status: {status_str}"
        )
        keyboard = [[InlineKeyboardButton("🗑️ Cancelar Agendamento", callback_data=f"ag_cancelar_{ag.id}")]]
        await context.bot.send_message(chat_id=user_id, text=mensagem, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

async def cancelar_agendamento_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    agendamento_id = int(query.data.split('_')[-1])
    user_id = query.from_user.id

    db = next(get_db())
    try:
        ag_para_cancelar = db.query(Agendamento).join(Usuario).filter(
            Agendamento.id == agendamento_id,
            Usuario.telegram_id == user_id
        ).first()

        if ag_para_cancelar:
            ag_para_cancelar.ativo = False
            db.commit()
            await query.edit_message_text("✅ Agendamento cancelado com sucesso.", reply_markup=None)
        else:
            await query.edit_message_text("❌ Erro: Agendamento não encontrado ou você não tem permissão.", reply_markup=None)
    except Exception as e:
        db.rollback()
        logger.error(f"Erro ao cancelar agendamento {agendamento_id}: {e}", exc_info=True)
        await query.edit_message_text("❌ Ocorreu um erro inesperado.", reply_markup=None)
    finally:
        db.close()

agendamento_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(agendamento_menu_callback, pattern='^agendamento_novo$')],
    states={
        ASK_TIPO: [CallbackQueryHandler(ask_tipo, pattern='^ag_tipo_')],
        ASK_DESCRICAO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_descricao)],
        ASK_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_valor)],
        ASK_CATEGORIA: [CallbackQueryHandler(ask_categoria, pattern='^ag_cat_')],
        ASK_PRIMEIRO_EVENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_primeiro_evento)],
        ASK_FREQUENCIA: [CallbackQueryHandler(ask_frequencia, pattern='^ag_freq_')],
        ASK_TIPO_RECORRENCIA: [CallbackQueryHandler(ask_tipo_recorrencia, pattern='^ag_rec_')],
        ASK_TOTAL_PARCELAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_total_parcelas)],
        CONFIRM_AGENDAMENTO: [
            CallbackQueryHandler(save_agendamento, pattern='^ag_confirm_save$'),
            CallbackQueryHandler(cancel, pattern='^ag_confirm_cancel$')
        ]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)

