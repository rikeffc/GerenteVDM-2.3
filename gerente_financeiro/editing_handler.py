import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

from database.database import (
    buscar_lancamentos_usuario, deletar_lancamento_por_id, atualizar_lancamento_por_id, get_db
)
from models import Categoria, Subcategoria
from .handlers import cancel, criar_teclado_colunas

logger = logging.getLogger(__name__)

# Estados da Conversa (COM NOVOS ESTADOS PARA CATEGORIA)
(
    CHOOSE_METHOD, AWAIT_SEARCH_QUERY, CHOOSE_LANCAMENTO,
    CHOOSE_FIELD_TO_EDIT, AWAIT_NEW_VALUE,
    AWAIT_NEW_CATEGORY, AWAIT_NEW_SUBCATEGORY
) = range(500, 507)


# =============================================================================
# 1. PONTO DE ENTRADA E SELEÇÃO DO MÉTODO DE BUSCA
# =============================================================================

async def start_editing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de edição de lançamento."""
    context.user_data.clear()
    keyboard = [
        [InlineKeyboardButton("📋 Ver 5 últimos", callback_data="method_last")],
        [InlineKeyboardButton("🔎 Buscar por nome", callback_data="method_search")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="method_cancel")]
    ]
    await update.message.reply_text(
        "Para editar um lançamento, primeiro precisamos encontrá-lo. Como prefere fazer?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CHOOSE_METHOD


async def choose_search_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa a escolha do método de busca (últimos ou por nome)."""
    query = update.callback_query
    await query.answer()
    method = query.data.split('_')[1]

    if method == "cancel":
        await query.edit_message_text("Operação cancelada.")
        return ConversationHandler.END

    if method == "search":
        await query.edit_message_text("Ok, digite parte do nome do lançamento que você procura (ex: iFood, Renner):")
        return AWAIT_SEARCH_QUERY

    if method == "last":
        lancamentos = buscar_lancamentos_usuario(query.from_user.id, limit=5)
        if not lancamentos:
            await query.edit_message_text("Não encontrei nenhum lançamento recente.")
            return ConversationHandler.END
        
        botoes = []
        for lanc in lancamentos:
            emoji = "🔴" if lanc.tipo == 'Saída' else "🟢"
            label = f"{emoji} {lanc.descricao[:20]} (R${lanc.valor:.2f})"
            botoes.append([InlineKeyboardButton(label, callback_data=f"select_{lanc.id}")])
        botoes.append([InlineKeyboardButton("❌ Cancelar", callback_data="select_cancel")])
        
        await query.edit_message_text("Selecione o lançamento para editar:", reply_markup=InlineKeyboardMarkup(botoes))
        return CHOOSE_LANCAMENTO


async def list_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o termo de busca, lista os resultados e pede a seleção."""
    search_term = update.message.text
    lancamentos = buscar_lancamentos_usuario(update.effective_user.id, limit=5, query=search_term)

    if not lancamentos:
        await update.message.reply_text(
            f"Não encontrei nenhum lançamento com '{search_term}'.\n"
            "Tente outro nome ou use /cancelar para sair."
        )
        return AWAIT_SEARCH_QUERY  # Permite ao usuário tentar de novo

    botoes = []
    for lanc in lancamentos:
        emoji = "🔴" if lanc.tipo == 'Saída' else "🟢"
        label = f"{emoji} {lanc.descricao[:20]} (R${lanc.valor:.2f})"
        botoes.append([InlineKeyboardButton(label, callback_data=f"select_{lanc.id}")])
    botoes.append([InlineKeyboardButton("❌ Cancelar", callback_data="select_cancel")])

    await update.message.reply_text("Encontrei estes lançamentos. Qual você quer editar?", reply_markup=InlineKeyboardMarkup(botoes))
    return CHOOSE_LANCAMENTO


# =============================================================================
# 2. SELEÇÃO DO LANÇAMENTO E EXIBIÇÃO DO PAINEL (COCKPIT)
# =============================================================================

async def _get_cockpit_text_and_keyboard(context: ContextTypes.DEFAULT_TYPE):
    """Função de apoio para gerar o texto e o teclado para o painel de edição."""
    edit_data = context.user_data['edit_data']
    desc = edit_data.get('descricao', 'N/A')
    valor = float(edit_data.get('valor', 0.0))
    data_obj = edit_data.get('data_transacao')
    data_str = data_obj.strftime('%d/%m/%Y') if isinstance(data_obj, datetime) else "N/A"
    categoria_str = edit_data.get('categoria_nome', 'N/A')
    subcategoria_str = edit_data.get('subcategoria_nome', '')
    
    categoria_full_str = f"{categoria_str} / {subcategoria_str}" if subcategoria_str else categoria_str

    text = (
        f"<b>⚙️ Editando Lançamento</b>\n\n"
        f"<b>Descrição:</b> {desc}\n"
        f"<b>Valor:</b> <code>R$ {valor:.2f}</code>\n"
        f"<b>Data:</b> {data_str}\n"
        f"<b>Categoria:</b> {categoria_full_str}\n\n"
        "Selecione o campo que deseja alterar ou uma ação:"
    )
    keyboard = [
        [InlineKeyboardButton("✏️ Descrição", callback_data="edit_descricao")],
        [InlineKeyboardButton("💰 Valor", callback_data="edit_valor")],
        [InlineKeyboardButton("🗓️ Data", callback_data="edit_data")],
        [InlineKeyboardButton("📂 Categoria", callback_data="edit_categoria")],
        [
            InlineKeyboardButton("✅ Salvar e Concluir", callback_data="edit_save"),
            InlineKeyboardButton("🗑️ Apagar Lançamento", callback_data="edit_delete")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)


async def select_lancamento_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Seleciona o lançamento e exibe o cockpit de edição."""
    query = update.callback_query
    await query.answer()
    
    if "cancel" in query.data:
        await query.edit_message_text("Operação cancelada.")
        return ConversationHandler.END

    lanc_id = int(query.data.split('_')[1])
    resultados = buscar_lancamentos_usuario(query.from_user.id, lancamento_id=lanc_id)

    if not resultados:
        await query.edit_message_text("❌ Erro: Lançamento não encontrado.")
        return ConversationHandler.END

    lanc = resultados[0]
    context.user_data['edit_data'] = {
        'id': lanc.id,
        'descricao': lanc.descricao,
        'valor': lanc.valor,
        'data_transacao': lanc.data_transacao,
        'id_categoria': lanc.id_categoria,
        'id_subcategoria': lanc.id_subcategoria,
        'categoria_nome': lanc.categoria.nome if lanc.categoria else "N/A",
        'subcategoria_nome': lanc.subcategoria.nome if lanc.subcategoria else ""
    }
    
    text, keyboard = await _get_cockpit_text_and_keyboard(context)
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)
    return CHOOSE_FIELD_TO_EDIT


# =============================================================================
# 3. LÓGICA DE EDIÇÃO DOS CAMPOS
# =============================================================================

async def choose_field_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a escolha do campo a ser editado e pede o novo valor ou mostra opções."""
    query = update.callback_query
    await query.answer()
    field = query.data.split('_')[1]

    if field == "save":
        lanc_id = context.user_data['edit_data']['id']
        # Remove os campos auxiliares antes de salvar
        data_to_update = {k: v for k, v in context.user_data['edit_data'].items() if k not in ['id', 'categoria_nome', 'subcategoria_nome']}
        
        atualizado = atualizar_lancamento_por_id(lanc_id, query.from_user.id, data_to_update)
        msg = "✅ Lançamento atualizado com sucesso!" if atualizado else "❌ Erro ao salvar."
        await query.edit_message_text(msg)
        return ConversationHandler.END

    if field == "delete":
        deletado = deletar_lancamento_por_id(context.user_data['edit_data']['id'], query.from_user.id)
        msg = "🗑️ Lançamento apagado com sucesso!" if deletado else "❌ Erro ao apagar."
        await query.edit_message_text(msg)
        return ConversationHandler.END
    
    if field == "categoria":
        db = next(get_db())
        categorias = db.query(Categoria).order_by(Categoria.nome).all()
        db.close()
        botoes = [InlineKeyboardButton(c.nome, callback_data=f"newcat_{c.id}") for c in categorias]
        teclado = criar_teclado_colunas(botoes, 2)
        await query.edit_message_text("Selecione a nova categoria:", reply_markup=InlineKeyboardMarkup(teclado))
        return AWAIT_NEW_CATEGORY

    # Para campos de texto
    context.user_data['field_to_edit'] = field
    prompt_map = {
        "descricao": "Qual a nova descrição?",
        "valor": "Qual o novo valor? (ex: 54.30)",
        "data": "Qual a nova data? (DD/MM/AAAA)"
    }
    await query.edit_message_text(prompt_map.get(field, "Envie o novo valor:"))
    return AWAIT_NEW_VALUE


async def receive_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o novo valor para campos de texto, atualiza o contexto e retorna ao cockpit."""
    field = context.user_data.get('field_to_edit')
    new_value_text = update.message.text
    try:
        if field == 'descricao':
            context.user_data['edit_data']['descricao'] = new_value_text
        elif field == 'valor':
            context.user_data['edit_data']['valor'] = float(new_value_text.replace(',', '.'))
        elif field == 'data':
            context.user_data['edit_data']['data_transacao'] = datetime.strptime(new_value_text, '%d/%m/%Y')
        
        text, keyboard = await _get_cockpit_text_and_keyboard(context)
        await update.message.reply_html(text, reply_markup=keyboard)
        return CHOOSE_FIELD_TO_EDIT
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Formato inválido. Por favor, tente novamente:")
        return AWAIT_NEW_VALUE


async def receive_new_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a nova categoria e pede a subcategoria ou volta ao cockpit."""
    query = update.callback_query
    await query.answer()
    category_id = int(query.data.split('_')[1])
    
    db = next(get_db())
    categoria_obj = db.query(Categoria).filter(Categoria.id == category_id).first()
    
    context.user_data['edit_data']['id_categoria'] = category_id
    context.user_data['edit_data']['categoria_nome'] = categoria_obj.nome
    
    subcategorias = db.query(Subcategoria).filter(Subcategoria.id_categoria == category_id).order_by(Subcategoria.nome).all()
    db.close()
    
    if not subcategorias:
        context.user_data['edit_data']['id_subcategoria'] = None
        context.user_data['edit_data']['subcategoria_nome'] = ""
        text, keyboard = await _get_cockpit_text_and_keyboard(context)
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)
        return CHOOSE_FIELD_TO_EDIT

    botoes = [InlineKeyboardButton(s.nome, callback_data=f"newsubcat_{s.id}") for s in subcategorias]
    teclado = criar_teclado_colunas(botoes, 2)
    teclado.append([InlineKeyboardButton("↩️ Sem Subcategoria", callback_data="newsubcat_0")])
    await query.edit_message_text("Selecione a nova subcategoria:", reply_markup=InlineKeyboardMarkup(teclado))
    return AWAIT_NEW_SUBCATEGORY


async def receive_new_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a nova subcategoria e retorna ao cockpit."""
    query = update.callback_query
    await query.answer()
    subcategory_id = int(query.data.split('_')[1])
    
    if subcategory_id == 0:
        context.user_data['edit_data']['id_subcategoria'] = None
        context.user_data['edit_data']['subcategoria_nome'] = ""
    else:
        db = next(get_db())
        sub_obj = db.query(Subcategoria).filter(Subcategoria.id == subcategory_id).first()
        db.close()
        context.user_data['edit_data']['id_subcategoria'] = subcategory_id
        context.user_data['edit_data']['subcategoria_nome'] = sub_obj.nome

    text, keyboard = await _get_cockpit_text_and_keyboard(context)
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard)
    return CHOOSE_FIELD_TO_EDIT


# =============================================================================
# 4. DEFINIÇÃO DO CONVERSATION HANDLER
# =============================================================================

edit_conv = ConversationHandler(
    entry_points=[CommandHandler('editar', start_editing)],
    states={
        CHOOSE_METHOD: [CallbackQueryHandler(choose_search_method, pattern=r'^method_')],
        AWAIT_SEARCH_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, list_search_results)],
        CHOOSE_LANCAMENTO: [CallbackQueryHandler(select_lancamento_to_edit, pattern=r'^select_')],
        CHOOSE_FIELD_TO_EDIT: [CallbackQueryHandler(choose_field_to_edit, pattern=r'^edit_')],
        AWAIT_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_value)],
        AWAIT_NEW_CATEGORY: [CallbackQueryHandler(receive_new_category, pattern=r'^newcat_')],
        AWAIT_NEW_SUBCATEGORY: [CallbackQueryHandler(receive_new_subcategory, pattern=r'^newsubcat_')]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)