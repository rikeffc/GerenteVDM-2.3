import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler
)

# Importando a função que vamos criar no próximo passo
from database.database import deletar_todos_dados_usuario
from .handlers import cancel # Reutilizamos a função de cancelamento

logger = logging.getLogger(__name__)

# Estado da conversa
(CONFIRM_DELETION,) = range(600, 601) # Usando um novo range para evitar conflitos

async def start_delete_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de exclusão de dados do usuário."""
    
    # Mensagem de aviso enfática, como você pediu
    text = (
        "🚨 <b>ATENÇÃO: AÇÃO IRREVERSÍVEL</b> 🚨\n\n"
        "Você tem <b>CERTEZA ABSOLUTA</b> que deseja apagar "
        "<u>todos os seus dados financeiros</u> do Maestro?\n\n"
        "Isso inclui:\n"
        "  - Todos os lançamentos\n"
        "  - Todas as metas\n"
        "  - Todos os agendamentos\n"
        "  - Todas as configurações de contas e perfil\n\n"
        "Uma vez confirmada, a exclusão é <b>PERMANENTE</b> e não poderá ser desfeita."
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🗑️ SIM, APAGAR TUDO", callback_data="delete_confirm_yes"),
            InlineKeyboardButton("👍 NÃO, CONTINUAR USANDO", callback_data="delete_confirm_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(text, reply_markup=reply_markup)
    
    return CONFIRM_DELETION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa a confirmação do usuário."""
    query = update.callback_query
    await query.answer()
    
    action = query.data
    
    if action == "delete_confirm_yes":
        user_id = query.from_user.id
        await query.edit_message_text("Processando sua solicitação... ⏳")
        
        # Chama a função do banco de dados para fazer a exclusão
        sucesso = deletar_todos_dados_usuario(telegram_id=user_id)
        
        if sucesso:
            await query.edit_message_text(
                "✅ Seus dados foram permanentemente apagados.\n\n"
                "Obrigado por usar o Maestro Financeiro. Se mudar de ideia, "
                "basta usar o comando /start para começar de novo."
            )
            logger.info(f"Usuário {user_id} apagou todos os seus dados.")
        else:
            await query.edit_message_text(
                "❌ Ocorreu um erro ao tentar apagar seus dados. "
                "Nossa equipe foi notificada."
            )
            
        return ConversationHandler.END
        
    else: # delete_confirm_no
        await query.edit_message_text("✅ Ufa! Seus dados estão seguros. Operação cancelada.")
        return ConversationHandler.END

# Cria o ConversationHandler para ser importado no bot.py
delete_user_conv = ConversationHandler(
    entry_points=[CommandHandler('apagartudo', start_delete_flow)],
    states={
        CONFIRM_DELETION: [CallbackQueryHandler(handle_confirmation, pattern='^delete_confirm_')]
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False
)