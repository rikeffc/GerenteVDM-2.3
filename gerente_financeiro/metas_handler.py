# gerente_financeiro/goals_handler.py
import logging
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters
)

# Importações de outros módulos do projeto
from database.database import (criar_novo_objetivo, listar_objetivos_usuario, deletar_objetivo_por_id, atualizar_objetivo_por_id)
from models import Objetivo
from .handlers import cancel, ASK_OBJETIVO_DESCRICAO, ASK_OBJETIVO_VALOR, ASK_OBJETIVO_PRAZO
import asyncio
logger = logging.getLogger(__name__)

#----------ESTADOS PARA EDIÇÃO DA META----------#
(ASK_EDIT_VALOR, ASK_EDIT_PRAZO) = range(300, 302)

# --- HANDLER DE METAS E OBJETIVOS ---

async def nova_meta_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia a conversa para criar uma nova meta com a nova formatação."""
    await update.message.reply_html(
        "🎯 <b>Qual é o seu próximo sonho financeiro?</b>\n"
        "<i>(ex: Viagem para o Japão, comprar um notebook novo)</i>"
    )
    return ASK_OBJETIVO_DESCRICAO

async def ask_objetivo_descricao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva a descrição da meta e pergunta o valor."""
    context.user_data['nova_meta_descricao'] = update.message.text
    await update.message.reply_html(
        "💰 <b>Quanto você precisará juntar para realizar esse objetivo?</b>\n"
        "<i>(Digite apenas o valor, ex: 1500.00)</i>"
    )
    return ASK_OBJETIVO_VALOR

async def ask_objetivo_valor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva o valor da meta e pergunta o prazo."""
    try:
        valor = float(update.message.text.replace(',', '.'))
        context.user_data['nova_meta_valor'] = valor
        await update.message.reply_html(
            "🗓️ <b>Qual a data limite para conquistar isso?</b>\n"
            "<i>(Use o formato DD/MM/AAAA)</i>"
        )
        return ASK_OBJETIVO_PRAZO
    except ValueError:
        await update.message.reply_text("⚠️ Valor inválido. Por favor, envie apenas números (ex: 1500.50).")
        return ASK_OBJETIVO_VALOR

async def save_objetivo_e_finaliza(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva a data, calcula a economia mensal, cria a meta no banco e finaliza a conversa."""
    try:
        data_final = datetime.strptime(update.message.text, '%d/%m/%Y').date()
        hoje = datetime.now().date()

        if data_final <= hoje:
            await update.message.reply_text("⚠️ A data precisa ser no futuro. Por favor, insira uma nova data.")
            return ASK_OBJETIVO_PRAZO

        descricao = context.user_data['nova_meta_descricao']
        valor_meta = context.user_data['nova_meta_valor']
        user_id = update.effective_user.id

        meses_restantes = (data_final.year - hoje.year) * 12 + (data_final.month - hoje.month)
        if meses_restantes <= 0: meses_restantes = 1
        economia_mensal = valor_meta / meses_restantes

        resultado = criar_novo_objetivo(user_id, descricao, valor_meta, data_final)
        
        if isinstance(resultado, Objetivo):
            mensagem_final = (
                "✅ <b>Sua meta está pronta!</b>\n"
                "Agora é hora de focar no seu plano:\n\n"
                f"🎯 <b>Objetivo:</b> {descricao}\n"
                f"💰 <b>Total a economizar:</b> R$ {valor_meta:.2f}\n"
                f"🗓️ <b>Prazo final:</b> {data_final.strftime('%d/%m/%Y')}\n"
                f"📆 <b>Você precisa guardar cerca de:</b> <code>R$ {economia_mensal:.2f}</code> por mês.\n\n"
                "Use /metas para acompanhar seu progresso!"
            )
            await update.message.reply_html(mensagem_final)
        elif resultado == "DUPLICATE":
            await update.message.reply_text(f"⚠️ Você já tem uma meta com o nome '{descricao}'. Por favor, escolha um nome diferente ou remova a meta existente em /metas.")
        else:
            await update.message.reply_text("❌ Houve um erro ao salvar sua meta. Tente novamente mais tarde.")

        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Formato de data inválido. Por favor, use DD/MM/AAAA.")
        return ASK_OBJETIVO_PRAZO

async def listar_metas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para listar as metas ativas do usuário com botão de remoção."""
    user_id = update.effective_user.id
    objetivos = listar_objetivos_usuario(user_id)
    
    if not objetivos:
        await update.message.reply_text("Você não tem nenhuma meta ativa no momento. Que tal criar uma com o comando /novameta?")
        return

    await update.message.reply_html("📊 <b>Suas Metas Ativas:</b>")
    for obj in objetivos:
        progresso = (float(obj.valor_atual) / float(obj.valor_meta)) * 100 if obj.valor_meta > 0 else 0
        blocos_cheios = int(progresso // 10)
        barra = "🟩" * blocos_cheios + "⬜️" * (10 - blocos_cheios)
        
        mensagem = (
            f"🎯 <b>{obj.descricao}</b>\n"
            f"💰 <code>R$ {obj.valor_atual:.2f} / R$ {obj.valor_meta:.2f}</code>\n"
            f"🗓️ Prazo: {obj.data_meta.strftime('%d/%m/%Y')}\n"
            f"{barra} {progresso:.1f}%"
        )

        keyboard = [[
            InlineKeyboardButton("✏️ Editar Meta", callback_data=f"editar_meta_{obj.id}"),
            InlineKeyboardButton("🗑️ Remover Meta", callback_data=f"deletar_meta_{obj.id}")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_html(mensagem, reply_markup=reply_markup)

async def deletar_meta_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa o clique no botão para deletar uma meta."""
    query = update.callback_query
    await query.answer()

    try:
        objetivo_id = int(query.data.split('_')[-1])
        user_id = query.from_user.id

        sucesso = deletar_objetivo_por_id(objetivo_id, user_id)

        if sucesso:
            await query.edit_message_text(text=f"✅ Meta removida com sucesso.", reply_markup=None)
        else:
            await query.edit_message_text(text="❌ Erro ao remover a meta. Ela pode já ter sido removida.", reply_markup=None)
    except (IndexError, ValueError):
        await query.edit_message_text(text="❌ Erro: ID da meta inválido.", reply_markup=None)
    except Exception as e:
        logger.error(f"Erro ao deletar meta via callback: {e}", exc_info=True)
        await query.edit_message_text(text="❌ Ocorreu um erro inesperado.", reply_markup=None)

async def edit_meta_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia a conversa para editar uma meta."""
    query = update.callback_query
    await query.answer()
    
    objetivo_id = int(query.data.split('_')[-1])
    context.user_data['meta_em_edicao_id'] = objetivo_id
    
    await query.edit_message_text(
        "✏️ Qual o <b>novo valor total</b> que você precisa para esta meta?\n"
        "<i><b>(Digite apenas o valor, ex: 2500)</b></i>",
        parse_mode='HTML'
    )
    return ASK_EDIT_VALOR

async def ask_edit_valor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva o novo valor e pergunta o novo prazo."""
    try:
        valor = float(update.message.text.replace(',', '.'))
        context.user_data['novo_valor_meta'] = valor
        await update.message.reply_html(
            "🗓️ Qual a <b>nova data limite</b> para conquistar isso?\n"
            "<i><b>(Use o formato DD/MM/AAAA)</b></i>"
        )
        return ASK_EDIT_PRAZO
    except ValueError:
        await update.message.reply_text("⚠️ Valor inválido. Por favor, envie apenas números (ex: 2500.50).")
        return ASK_EDIT_VALOR

async def ask_edit_prazo_and_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Salva o novo prazo, atualiza a meta no banco e finaliza a conversa."""
    try:
        nova_data_final = datetime.strptime(update.message.text, '%d/%m/%Y').date()
        hoje = datetime.now().date()

        if nova_data_final <= hoje:
            await update.message.reply_text("⚠️ A data precisa ser no futuro. Por favor, insira uma nova data.")
            return ASK_EDIT_PRAZO

        # Recupera os dados da conversa
        objetivo_id = context.user_data['meta_em_edicao_id']
        novo_valor = context.user_data['novo_valor_meta']
        user_id = update.effective_user.id

        # Usa a nova função do database.py para atualizar
        objetivo_atualizado = atualizar_objetivo_por_id(objetivo_id, user_id, novo_valor, nova_data_final)

        if objetivo_atualizado:
            # 1. Envia uma mensagem de sucesso simples e temporária.
            await update.message.reply_html(
                "✅ <b>Meta atualizada com sucesso!</b>\n\n"
                "<i>Atualizando sua lista de metas...</i>"
            )
            
            # 2. Espera um pouco para a UX ficar mais agradável.
            await asyncio.sleep(1.5)
            
            # 3. Chama a função que lista as metas para mostrar o resultado.
            await listar_metas_command(update, context)
            
        else:
            await update.message.reply_text("❌ Houve um erro ao tentar atualizar sua meta. Tente novamente.")

        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Formato de data inválido. Por favor, use DD/MM/AAAA.")
        return ASK_EDIT_PRAZO        


# --- DEFINIÇÃO DO CONVERSATION HANDLER DE METAS ---

objetivo_conv = ConversationHandler(
    entry_points=[CommandHandler('novameta', nova_meta_start)],
    states={
        ASK_OBJETIVO_DESCRICAO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_objetivo_descricao)],
        ASK_OBJETIVO_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_objetivo_valor)],
        ASK_OBJETIVO_PRAZO: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_objetivo_e_finaliza)],
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)

edit_meta_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(edit_meta_start, pattern='^editar_meta_')],
    states={
        ASK_EDIT_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_edit_valor)],
        ASK_EDIT_PRAZO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_edit_prazo_and_save)],
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)