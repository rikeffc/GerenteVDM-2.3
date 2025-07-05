# gerente_financeiro/onboarding_handler.py
import logging
from datetime import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
)

from database.database import get_db, get_or_create_user # <-- Importação adicionada
from models import Usuario, Conta
from .handlers import cancel

logger = logging.getLogger(__name__)

# --- ESTADOS DA CONVERSA DE CONFIGURAÇÃO UNIFICADA ---
# ALTERAÇÃO: Adicionamos dois novos estados e expandimos o range
(
    MENU_PRINCIPAL,
    ADD_CONTA_NOME, ASK_ADD_ANOTHER_CONTA,  
    ADD_CARTAO_NOME, ADD_CARTAO_FECHAMENTO, ADD_CARTAO_VENCIMENTO, ASK_ADD_ANOTHER_CARTAO, 
    ASK_HORARIO,
    PERFIL_ASK_RISCO, PERFIL_ASK_OBJETIVO, PERFIL_ASK_HABITO
) = range(400, 411) 
# --- FUNÇÕES DE MENU E NAVEGAÇÃO ---

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o menu principal de configuração de forma consistente."""
    query = update.callback_query
    effective_user = update.effective_user
    if query:
        effective_user = query.from_user
    
    db = next(get_db())
    # Usamos get_or_create_user para garantir que o usuário sempre exista
    user_db = get_or_create_user(db, effective_user.id, effective_user.full_name)
    horario_atual = user_db.horario_notificacao.strftime('%H:%M') if user_db.horario_notificacao else "09:00"
    perfil_atual = user_db.perfil_investidor if user_db.perfil_investidor else "Não definido"
    db.close()

    text = (
        f"⚙️ <b>Painel de Configuração</b>\n\n"
        f"Seu horário para lembretes é <b>{horario_atual}</b>.\n"
        f"Seu perfil de investidor é <b>{perfil_atual}</b>.\n\n"
        "Use os botões para personalizar suas preferências."
    )
    keyboard = [
        [InlineKeyboardButton("👤 Gerenciar Perfil de Investidor", callback_data="config_perfil")],
        [InlineKeyboardButton("🏦 Gerenciar Contas", callback_data="config_contas")],
        [InlineKeyboardButton("💳 Gerenciar Cartões", callback_data="config_cartoes")],
        [InlineKeyboardButton("⏰ Alterar Horário de Lembretes", callback_data="config_horario")],
        [InlineKeyboardButton("✅ Concluir", callback_data="config_concluir")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
    else:
        await update.message.reply_html(text, reply_markup=reply_markup)
    
    return MENU_PRINCIPAL

# --- FLUXO PRINCIPAL ---

async def configurar_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Inicia o fluxo de configuração/onboarding."""
    # Garante que o usuário exista no banco de dados ANTES de qualquer outra coisa
    db = next(get_db())
    get_or_create_user(db, update.effective_user.id, update.effective_user.full_name)
    db.close()
    
    await update.message.reply_html(
        "👋 Olá! Para que eu possa ser seu melhor assistente financeiro, vamos configurar seu ecossistema. "
        "Essa etapa é rápida e vai me ajudar a personalizar sua experiência. Vamos lá? 🚀"
    )
    return await show_main_menu(update, context)

async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa os cliques nos botões do menu principal."""
    query = update.callback_query
    await query.answer()
    action = query.data.split('_')[1]

    if action == "concluir":
        await query.edit_message_text("✅ Configurações salvas!", reply_markup=None)
        return ConversationHandler.END

    if action == "perfil":
        return await start_perfil_flow(update, context)

    if action == "contas":
        await query.edit_message_text(
            "🏦 Vamos cadastrar suas contas (conta corrente, poupança, etc.).\n\n"
            "Qual o nome da sua primeira conta? <b>(ex: Itaú, Nubank, Bradesco)</b>",
            parse_mode='HTML'
        )
        return ADD_CONTA_NOME
        
    if action == "cartoes":
        await query.edit_message_text(
            "💳 Agora, vamos cadastrar seus cartões de crédito.\n\n"
            "Qual o nome do seu principal cartão? <b>(ex: Inter Gold)</b>",
            parse_mode='HTML'
        )
        return ADD_CARTAO_NOME
        
    if action == "horario":
        await query.edit_message_text(
            "📆 Por favor, digite o horário em que deseja receber seus lembretes diários.\n\n"
            "Use o formato <b>24h (HH:MM)</b>, por exemplo: <b>08:30</b>",
            parse_mode='HTML'
        )
        return ASK_HORARIO

# --- FLUXO DE PERFIL DE INVESTIDOR ---

async def start_perfil_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['perfil_pontos'] = 0
    text = (
        "<b>Pergunta 1 de 3: Tolerância ao Risco</b>\n\n"
        "Se seus investimentos caíssem 20% em um mês, o que você faria?"
    )
    keyboard = [
        [InlineKeyboardButton("A) Venderia tudo para evitar mais perdas", callback_data="perfil_risco_1")],
        [InlineKeyboardButton("B) Esperaria o mercado se recuperar", callback_data="perfil_risco_2")],
        [InlineKeyboardButton("C) Aproveitaria para comprar mais", callback_data="perfil_risco_3")]
    ]
    await update.callback_query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    return PERFIL_ASK_RISCO

async def ask_perfil_risco(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pontos = int(query.data.split('_')[-1])
    context.user_data['perfil_pontos'] += pontos
    text = (
        "<b>Pergunta 2 de 3: Objetivo Principal</b>\n\n"
        "Qual é seu principal objetivo com o dinheiro investido?"
    )
    keyboard = [
        [InlineKeyboardButton("A) Segurança e proteção do capital", callback_data="perfil_objetivo_1")],
        [InlineKeyboardButton("B) Crescimento estável no médio prazo", callback_data="perfil_objetivo_2")],
        [InlineKeyboardButton("C) Alto retorno no longo prazo, mesmo com riscos", callback_data="perfil_objetivo_3")]
    ]
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    return PERFIL_ASK_OBJETIVO

async def ask_perfil_objetivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pontos = int(query.data.split('_')[-1])
    context.user_data['perfil_pontos'] += pontos
    text = (
        "<b>Pergunta 3 de 3: Hábito Financeiro</b>\n\n"
        "Você costuma guardar dinheiro todos os meses?"
    )
    keyboard = [
        [InlineKeyboardButton("A) Sim, com disciplina", callback_data="perfil_habito_3")],
        [InlineKeyboardButton("B) Só quando sobra", callback_data="perfil_habito_2")],
        [InlineKeyboardButton("C) Quase nunca consigo guardar", callback_data="perfil_habito_1")]
    ]
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
    return PERFIL_ASK_HABITO

async def finalizar_perfil(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    pontos = int(query.data.split('_')[-1])
    total_pontos = context.user_data.get('perfil_pontos', 0) + pontos

    if total_pontos <= 4: perfil = 'Conservador'
    elif total_pontos <= 7: perfil = 'Moderado'
    else: perfil = 'Arrojado'
    
    db = next(get_db())
    try:
        # A consulta agora vai funcionar, pois o usuário foi criado no início
        user_db = db.query(Usuario).filter(Usuario.telegram_id == query.from_user.id).first()
        user_db.perfil_investidor = perfil
        db.commit()
        await query.edit_message_text(f"✅ Perfil definido como: <b>{perfil}</b>!\n\nRetornando ao menu...", parse_mode='HTML', reply_markup=None)
    finally:
        db.close()
        context.user_data.pop('perfil_pontos', None)
    
    import asyncio
    await asyncio.sleep(1.5)
    return await show_main_menu(update, context)

# --- OUTROS SUB-FLUXOS ---

async def add_conta_nome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nome_conta = update.message.text
    db = next(get_db())
    try:
        usuario_db = db.query(Usuario).filter(Usuario.telegram_id == update.effective_user.id).first()
        nova_conta = Conta(id_usuario=usuario_db.id, nome=nome_conta, tipo="Conta")
        db.add(nova_conta)
        db.commit()
        # Em vez de voltar ao menu, perguntamos se o usuário quer adicionar outra conta.
        keyboard = [
            [InlineKeyboardButton("➕ Sim, adicionar outra", callback_data="add_another_conta_sim")],
            [InlineKeyboardButton("⬅️ Não, voltar ao menu", callback_data="add_another_conta_nao")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_html(
            f"✅ Conta '<b>{nome_conta}</b>' adicionada!\n\nDeseja adicionar outra conta?",
            reply_markup=reply_markup
        )

        
        return ASK_ADD_ANOTHER_CONTA
 
    finally:
        db.close()

async def add_cartao_nome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['novo_cartao_nome'] = update.message.text
    await update.message.reply_html("🗓️ Qual o <b>dia de fechamento</b> da fatura? <b>(Apenas o número)</b>")
    return ADD_CARTAO_FECHAMENTO

async def add_cartao_fechamento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data['novo_cartao_fechamento'] = int(update.message.text)
        await update.message.reply_html("🗓️ E qual o <b>dia de vencimento</b> da fatura?")
        return ADD_CARTAO_VENCIMENTO
    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Por favor, insira um número válido.")
        return ADD_CARTAO_FECHAMENTO

async def add_cartao_vencimento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dia_vencimento = int(update.message.text)
        nome_cartao = context.user_data['novo_cartao_nome']
        dia_fechamento = context.user_data['novo_cartao_fechamento']
        db = next(get_db())
        try:
            usuario_db = db.query(Usuario).filter(Usuario.telegram_id == update.effective_user.id).first()
            novo_cartao = Conta(
                id_usuario=usuario_db.id, nome=nome_cartao, tipo="Cartão de Crédito",
                dia_fechamento=dia_fechamento, dia_vencimento=dia_vencimento
            )
            db.add(novo_cartao)
            db.commit()
            # Em vez de voltar ao menu, perguntamos se o usuário quer adicionar outro cartão.
            keyboard = [
                [InlineKeyboardButton("➕ Sim, adicionar outro", callback_data="add_another_cartao_sim")],
                [InlineKeyboardButton("⬅️ Não, voltar ao menu", callback_data="add_another_cartao_nao")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_html(
                f"✅ Cartão <b>{nome_cartao}</b> adicionado!\n\nDeseja adicionar outro cartão de crédito?",
                reply_markup=reply_markup
            )
            
            return ASK_ADD_ANOTHER_CARTAO

        finally:
            db.close()
            context.user_data.clear()

    except (ValueError, TypeError):
        await update.message.reply_text("⚠️ Por favor, insira um número válido.")
        return ADD_CARTAO_VENCIMENTO
    
async def handle_add_another_cartao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa a decisão de adicionar ou não outro cartão."""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "add_another_cartao_sim":
        # Se sim, voltamos para o início do fluxo de cartão.
        await query.edit_message_text("Ok! Qual o nome do próximo cartão? (ex: XP Visa Infinite)")
        return ADD_CARTAO_NOME
    else: # "add_another_cartao_nao"
        # Se não, voltamos para o menu principal.
        return await show_main_menu(update, context)
    
async def handle_add_another_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processa a decisão de adicionar ou não outra conta."""
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "add_another_conta_sim":
        # Se sim, apenas pedimos o nome da próxima conta e voltamos ao estado ADD_CONTA_NOME.
        await query.edit_message_text("🏦 Beleza! Manda o nome da próxima <b>conta</b>?")
        return ADD_CONTA_NOME
    else: # "add_another_conta_nao"
        # Se não, voltamos para o menu principal.
        return await show_main_menu(update, context)

async def save_horario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        novo_horario_obj = time.fromisoformat(update.message.text)
        db = next(get_db())
        try:
            user_db = db.query(Usuario).filter(Usuario.telegram_id == update.effective_user.id).first()
            user_db.horario_notificacao = novo_horario_obj
            db.commit()
            await update.message.reply_html(f"✅ Horário de lembretes atualizado para <b>{update.message.text}</b>.")
        finally:
            db.close()
        return await show_main_menu(update, context)
    except ValueError:
        await update.message.reply_text("⚠️ Formato inválido. Use HH:MM (ex: 09:00).")
        return ASK_HORARIO

# --- CONVERSATION HANDLER UNIFICADO ---
configurar_conv = ConversationHandler(
    entry_points=[CommandHandler('configurar', configurar_start)],
    states={
        MENU_PRINCIPAL: [CallbackQueryHandler(menu_callback_handler, pattern='^config_')],
        
        # ---FLUXO DE CONTAS ---
        ADD_CONTA_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_conta_nome)],
        ASK_ADD_ANOTHER_CONTA: [CallbackQueryHandler(handle_add_another_conta, pattern='^add_another_conta_')],

        # ---FLUXO DE CARTÕES ---
        ADD_CARTAO_NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cartao_nome)],
        ADD_CARTAO_FECHAMENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cartao_fechamento)],
        ADD_CARTAO_VENCIMENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_cartao_vencimento)],
        ASK_ADD_ANOTHER_CARTAO: [CallbackQueryHandler(handle_add_another_cartao, pattern='^add_another_cartao_')],

        # --- OUTROS ESTADOS ---
        ASK_HORARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_horario)],
        PERFIL_ASK_RISCO: [CallbackQueryHandler(ask_perfil_risco, pattern='^perfil_risco_')],
        PERFIL_ASK_OBJETIVO: [CallbackQueryHandler(ask_perfil_objetivo, pattern='^perfil_objetivo_')],
        PERFIL_ASK_HABITO: [CallbackQueryHandler(finalizar_perfil, pattern='^perfil_habito_')],
    },
    fallbacks=[CommandHandler('cancelar', cancel)],
    per_message=False,
)