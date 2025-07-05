import logging
from datetime import datetime, timedelta, time
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import or_

from database.database import get_db
from models import Agendamento, Lancamento, Usuario

logger = logging.getLogger(__name__)

async def agendar_notificacoes_diarias(context):
    """
    Job "Mestre" que roda uma vez de madrugada.
    Ele encontra todos os usuários que precisam de notificações hoje e agenda
    um job individual para cada um na sua hora preferida.
    """
    logger.info("JOB MESTRE: Iniciando agendamento de notificações individuais para o dia.")
    hoje = datetime.now().date()
    
    db: Session = next(get_db())
    try:
        # Query para encontrar todos os usuários que têm agendamentos ativos para hoje ou amanhã
        usuarios_com_agendamentos = db.query(Usuario).join(Agendamento).filter(
            Agendamento.ativo == True,
            or_(
                Agendamento.proxima_data_execucao == hoje,
                Agendamento.proxima_data_execucao == hoje + timedelta(days=1)
            )
        ).distinct().all()

        if not usuarios_com_agendamentos:
            logger.info("JOB MESTRE: Nenhum usuário com agendamentos para hoje ou amanhã. Encerrando.")
            return

        for usuario in usuarios_com_agendamentos:
            # Remove jobs antigos para o mesmo usuário para evitar duplicatas se o bot reiniciar
            jobs_antigos = context.job_queue.get_jobs_by_name(f"notificacao_diaria_{usuario.id}")
            for job in jobs_antigos:
                job.schedule_removal()
                logger.info(f"Removendo job antigo: {job.name}")

            # Agenda um job único para rodar HOJE, na hora que o usuário escolheu
            hora_preferida = usuario.horario_notificacao
            horario_execucao = datetime.combine(hoje, hora_preferida)
            
            context.job_queue.run_once(
                enviar_notificacoes_e_processar_agendamentos,
                when=horario_execucao,
                data={'user_id': usuario.id},
                name=f"notificacao_diaria_{usuario.id}"
            )
        
        logger.info(f"JOB MESTRE: {len(usuarios_com_agendamentos)} jobs individuais agendados para hoje.")

    except Exception as e:
        logger.error(f"Erro CRÍTICO no job mestre de agendamento: {e}", exc_info=True)
    finally:
        db.close()

async def enviar_notificacoes_e_processar_agendamentos(context):
    """
    Job individual que roda na hora preferida do usuário.
    Ele envia as notificações e processa os lançamentos apenas para aquele usuário.
    """
    user_id = context.job.data['user_id']
    logger.info(f"JOB INDIVIDUAL: Executando para o usuário ID: {user_id}")
    
    hoje = datetime.now().date()
    amanha = hoje + timedelta(days=1)
    
    db: Session = next(get_db())
    try:
        # Busca os agendamentos relevantes APENAS para este usuário
        agendamentos_do_usuario = db.query(Agendamento).filter(
            Agendamento.id_usuario == user_id,
            Agendamento.ativo == True
        ).all()

        for ag in agendamentos_do_usuario:
            # 1. Enviar lembrete de amanhã
            if ag.proxima_data_execucao == amanha:
                tipo_str = "receber" if ag.tipo == "Entrada" else "pagar"
                msg = f"🔔 Lembrete: Amanhã é o dia de {tipo_str} '{ag.descricao}' no valor de R$ {ag.valor:.2f}."
                await context.bot.send_message(chat_id=ag.usuario.telegram_id, text=msg)

            # 2. Enviar lembrete e EXECUTAR o de hoje
            if ag.proxima_data_execucao == hoje:
                tipo_str_hoje = "Recebimento" if ag.tipo == "Entrada" else "Pagamento"
                msg_hoje = f"⏰ Hoje é o dia do seu agendamento: {tipo_str_hoje} de '{ag.descricao}' (R$ {ag.valor:.2f})."
                await context.bot.send_message(chat_id=ag.usuario.telegram_id, text=msg_hoje)

                # Cria o lançamento real
                novo_lancamento = Lancamento(
                    id_usuario=ag.id_usuario,
                    descricao=f"{ag.descricao} (Agendado)",
                    valor=ag.valor,
                    tipo=ag.tipo,
                    data_transacao=datetime.combine(hoje, time.min),
                    forma_pagamento="Agendado",
                    id_categoria=ag.id_categoria,
                    id_subcategoria=ag.id_subcategoria
                )
                db.add(novo_lancamento)
                
                # Atualiza o agendamento
                ag.parcela_atual += 1
                if ag.total_parcelas and ag.parcela_atual >= ag.total_parcelas:
                    ag.ativo = False
                else:
                    if ag.frequencia == 'mensal':
                        ag.proxima_data_execucao += relativedelta(months=1)
                    elif ag.frequencia == 'semanal':
                        ag.proxima_data_execucao += timedelta(weeks=1)
                    elif ag.frequencia == 'unico':
                        ag.ativo = False
        
        db.commit()
        logger.info(f"JOB INDIVIDUAL: Processamento concluído para o usuário ID: {user_id}")
    except Exception as e:
        logger.error(f"Erro no job individual para o usuário {user_id}: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()