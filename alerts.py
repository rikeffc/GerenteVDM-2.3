import logging
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.orm import Session
from telegram import Update
from telegram.ext import CallbackContext, ContextTypes

# Importação corrigida e explícita
from database.database import get_db, listar_todos_objetivos_ativos, atualizar_valor_objetivo
from models import Lancamento, Usuario, Objetivo

logger = logging.getLogger(__name__)

# --- ALERTA DE ORÇAMENTO DIÁRIO ---

async def check_budget_overrun(context: CallbackContext):
    """Verifica se o orçamento foi ultrapassado e envia um alerta."""
    job_data = context.job.data
    user_telegram_id = job_data["user_telegram_id"]
    budget_limit = job_data["budget_limit"]
    
    db: Session = next(get_db())
    try:
        start_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        total_expenses_query = db.query(func.sum(Lancamento.valor)).join(Usuario).filter(
            Usuario.telegram_id == user_telegram_id,
            Lancamento.tipo == 'Saída',
            Lancamento.data_transacao >= start_of_month
        )
        total = total_expenses_query.scalar() or 0.0

        if total > budget_limit:
            await context.bot.send_message(
                chat_id=user_telegram_id, 
                text=f"⚠️ **Alerta de Orçamento!**\n\nVocê ultrapassou seu limite mensal de `R${budget_limit:.2f}`.\nSeu total de despesas este mês já é de `R${total:.2f}`.",
                parse_mode='Markdown'
            )
    except Exception as e:
        logging.error(f"Erro dentro do job check_budget_overrun: {e}", exc_info=True)
    finally:
        db.close()

async def schedule_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Agenda um alerta de orçamento diário para o usuário."""
    if not context.args:
        await update.message.reply_text("⚠️ Por favor, informe o limite de orçamento mensal. Exemplo: `/alerta 1500`")
        return

    try:
        budget_limit = float(context.args[0])
        user_id = update.effective_user.id
        job_name = f"budget_alert_{user_id}"
        
        current_jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()
            logging.info(f"Job de alerta antigo removido para o usuário {user_id}")

        context.job_queue.run_daily(
            check_budget_overrun, 
            time=datetime.strptime("09:00", "%H:%M").time(),
            data={"user_telegram_id": user_id, "budget_limit": budget_limit},
            name=job_name
        )
        
        await update.message.reply_text(f"✅ Alerta de orçamento agendado! Você será notificado diariamente se suas despesas mensais ultrapassarem `R${budget_limit:.2f}`.", parse_mode='Markdown')
    except ValueError:
        await update.message.reply_text("⚠️ O limite de orçamento deve ser um número. Exemplo: `/alerta 1500`")
    except Exception as e:
        logging.error(f"Erro ao agendar alerta: {e}", exc_info=True)
        await update.message.reply_text("❌ Ocorreu um erro ao agendar o alerta.")


# --- ACOMPANHAMENTO SEMANAL DE METAS ---

async def checar_objetivos_semanal(context: ContextTypes.DEFAULT_TYPE):
    """
    Job que roda semanalmente para checar o progresso das metas de todos os usuários.
    """
    logger.info("Executando job semanal de verificação de metas...")
    objetivos_ativos = listar_todos_objetivos_ativos()
    
    for objetivo in objetivos_ativos:
        try:
            db: Session = next(get_db())
            total_entradas_query = db.query(func.sum(Lancamento.valor)).filter(
                Lancamento.id_usuario == objetivo.id_usuario,
                Lancamento.tipo == 'Entrada',
                Lancamento.data_transacao >= objetivo.criado_em
            )
            total_saidas_query = db.query(func.sum(Lancamento.valor)).filter(
                Lancamento.id_usuario == objetivo.id_usuario,
                Lancamento.tipo == 'Saída',
                Lancamento.data_transacao >= objetivo.criado_em
            )
            total_entradas = total_entradas_query.scalar() or 0.0
            total_saidas = total_saidas_query.scalar() or 0.0
            economia_atual = float(total_entradas - total_saidas)
            db.close()

            if economia_atual > float(objetivo.valor_atual):
                 atualizar_valor_objetivo(objetivo.id, economia_atual)
                 objetivo.valor_atual = economia_atual

            if objetivo.valor_atual >= objetivo.valor_meta:
                mensagem = (
                    f"🎉✨ **VITÓRIA! VOCÊ CONSEGUIU!** ✨🎉\n\n"
                    f"Parabéns! Você alcançou sua meta de **'{objetivo.descricao}'**!\n\n"
                    f"Você juntou **R$ {objetivo.valor_atual:.2f}**, superando seu objetivo de R$ {objetivo.valor_meta:.2f}. "
                    f"Isso é a prova da sua disciplina e foco. Estamos orgulhosos de você! 🚀"
                )
                await context.bot.send_message(chat_id=objetivo.usuario.telegram_id, text=mensagem, parse_mode='Markdown')
                continue

            hoje = datetime.now().date()
            data_final = objetivo.data_meta
            dias_restantes = (data_final - hoje).days
            progresso = (float(objetivo.valor_atual) / float(objetivo.valor_meta)) * 100

            mensagem = ""
            if dias_restantes < 0:
                mensagem = (
                    f"⏳ *O tempo acabou para a sua meta '{objetivo.descricao}'...*\n\n"
                    f"Não desanime! Você chegou em **{progresso:.1f}%**. "
                    f"Use o que aprendeu para começar de novo, mais forte e mais sábio!"
                )
            elif progresso >= 75:
                mensagem = (
                    f"🔥 **VOCÊ ESTÁ QUASE LÁ!** 🔥\n\n"
                    f"Sua meta '{objetivo.descricao}' está com **{progresso:.1f}%** de progresso! Faltam apenas **{dias_restantes} dias**.\n"
                    f"Mantenha o foco total agora! 💪"
                )
            elif progresso >= 40:
                mensagem = (
                    f"👍 *Bom progresso na sua meta '{objetivo.descricao}'!*\n\n"
                    f"Você já alcançou **{progresso:.1f}%** e ainda tem **{dias_restantes} dias** pela frente.\n"
                    f"Continue com a disciplina e a consistência!"
                )
            
            if mensagem:
                await context.bot.send_message(chat_id=objetivo.usuario.telegram_id, text=mensagem, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Erro ao processar alerta para o objetivo {objetivo.id}: {e}")