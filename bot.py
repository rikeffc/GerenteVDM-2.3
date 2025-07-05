# bot.py
import logging
import warnings
import google.generativeai as genai
import os
from datetime import time
from gerente_financeiro.extrato_handler import criar_conversation_handler_extrato
from sqlalchemy.orm import Session, joinedload
from telegram.warnings import PTBUserWarning
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    CallbackQueryHandler, ConversationHandler, ApplicationBuilder, ContextTypes
)

# --- IMPORTS DO PROJETO ---
import config
from database.database import get_db, popular_dados_iniciais, criar_tabelas
from models import *
from alerts import schedule_alerts, checar_objetivos_semanal
from jobs import agendar_notificacoes_diarias

# --- IMPORTS DOS HANDLERS (AGORA ORGANIZADOS) ---
from gerente_financeiro.handlers import (
    create_gerente_conversation_handler, 
    create_onboarding_conversation_handler,
    handle_analise_impacto_callback,  
    help_callback, 
    help_command,
    cancel 
)
from gerente_financeiro.agendamentos_handler import (
    agendamento_start, agendamento_conv, agendamento_menu_callback, cancelar_agendamento_callback
)
from gerente_financeiro.metas_handler import (
    objetivo_conv, listar_metas_command, deletar_meta_callback, edit_meta_conv
)
from gerente_financeiro.onboarding_handler import configurar_conv
from gerente_financeiro.editing_handler import edit_conv
from gerente_financeiro.graficos import grafico_conv
from gerente_financeiro.relatorio_handler import relatorio_handler
from gerente_financeiro.manual_entry_handler import manual_entry_conv
from gerente_financeiro.contact_handler import contact_conv
from gerente_financeiro.delete_user_handler import delete_user_conv
from gerente_financeiro.fatura_handler import fatura_conv  # <-- A importação correta e única

# --- CONFIGURAÇÃO INICIAL ---
warnings.filterwarnings("ignore", category=PTBUserWarning)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- FUNÇÕES PRINCIPAIS DO BOT ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Loga os erros e envia uma mensagem de erro genérica."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Ocorreu um erro inesperado. Minha equipe já foi notificada.")
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")

def main() -> None:
    """Função principal que monta e executa o bot."""
    logger.info("Iniciando o bot...")

    # Configuração do Banco de Dados
    try:
        criar_tabelas()
        db: Session = next(get_db())
        popular_dados_iniciais(db)
        db.close()
        logger.info("Banco de dados pronto.")
    except Exception as e:
        logger.critical(f"Falha crítica na configuração do banco de dados: {e}", exc_info=True)
        return

    # Configuração da API do Gemini
    try:
        genai.configure(api_key=config.GEMINI_API_KEY)
        logger.info("API do Gemini configurada.")
    except Exception as e:
        logger.critical(f"Falha ao configurar a API do Gemini: {e}")
        return

    # Construção da Aplicação do Bot
    application = ApplicationBuilder().token(config.TELEGRAM_TOKEN).build()
    logger.info("Aplicação do bot criada.")

    
    gerente_conv = create_gerente_conversation_handler()
    start_conv = create_onboarding_conversation_handler()
    
    # Adicionando todos os handlers à aplicação
    logger.info("Adicionando handlers...")
    
    # Handlers de Conversa (ConversationHandler)
    application.add_handler(start_conv)
    application.add_handler(gerente_conv)
    application.add_handler(manual_entry_conv)
    application.add_handler(fatura_conv)        # Adicionado aqui
    application.add_handler(delete_user_conv)
    application.add_handler(contact_conv)
    application.add_handler(grafico_conv)
    application.add_handler(objetivo_conv)
    application.add_handler(edit_meta_conv)
    application.add_handler(agendamento_conv)
    application.add_handler(configurar_conv)
    application.add_handler(edit_conv)
    application.add_handler(criar_conversation_handler_extrato())
    
    # Handlers de Comando (CommandHandler)
    application.add_handler(relatorio_handler)  # É um CommandHandler, não uma conversa
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("alerta", schedule_alerts))
    application.add_handler(CommandHandler("metas", listar_metas_command))
    application.add_handler(CommandHandler("agendar", agendamento_start))
    
    # Handlers de Callback (CallbackQueryHandler) para menus e botões
    application.add_handler(CallbackQueryHandler(help_callback, pattern="^help_"))
    application.add_handler(CallbackQueryHandler(handle_analise_impacto_callback, pattern="^analise_"))
    application.add_handler(CallbackQueryHandler(deletar_meta_callback, pattern="^deletar_meta_"))
    application.add_handler(CallbackQueryHandler(agendamento_menu_callback, pattern="^agendamento_"))
    application.add_handler(CallbackQueryHandler(cancelar_agendamento_callback, pattern="^ag_cancelar_"))
    
    # Handler de Erro
    application.add_error_handler(error_handler)
    logger.info("Todos os handlers adicionados com sucesso.")
    
    # Configuração e inicialização dos Jobs agendados
    job_queue = application.job_queue
    job_queue.run_daily(checar_objetivos_semanal, time=time(hour=10, minute=0), days=(6,), name="checar_metas_semanalmente")
    job_queue.run_daily(agendar_notificacoes_diarias, time=time(hour=1, minute=0), name="agendador_mestre_diario")
    logger.info("Jobs de metas e agendamentos configurados.")
    
    # Inicia o bot
    logger.info("Bot pronto. Iniciando polling...")
    application.run_polling()
    logger.info("Bot foi encerrado.")

if __name__ == '__main__':
    main()