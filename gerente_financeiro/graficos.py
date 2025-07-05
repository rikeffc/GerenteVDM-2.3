# gerente_financeiro/graficos.py
import logging
from contextlib import contextmanager
from enum import IntEnum
from functools import lru_cache
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    CallbackQueryHandler,
)
from telegram.error import TelegramError

from database.database import get_db, DatabaseError, ServiceError  # Agora importando do database.py
from . import services

logger = logging.getLogger(__name__)

# Estados da conversa usando Enum para melhor organização
class ChartStates(IntEnum):
    CHART_MENU = 20

# Mapeamento explícito dos parâmetros de cada gráfico
CHART_PARAMS = {
    "grafico_categoria_pizza": {"agrupar_por": "categoria", "tipo_grafico": "pizza"},
    "grafico_categoria_barra_h": {"agrupar_por": "categoria", "tipo_grafico": "barra_h"},
    "grafico_data_linha": {"agrupar_por": "data", "tipo_grafico": "linha"},
    "grafico_fluxo_caixa_area": {"agrupar_por": "fluxo_caixa", "tipo_grafico": "area"},
    "grafico_projecao_barra_linha": {"agrupar_por": "projecao", "tipo_grafico": "barra_linha"},
    "grafico_forma_pagamento_pizza": {"agrupar_por": "forma_pagamento", "tipo_grafico": "pizza"},
}

# Cache para lançamentos (5 minutos de TTL)
CACHE_TTL_MINUTES = 5
_cache_timestamps = {}

@contextmanager
def get_db_context():
    """Context manager para garantir fechamento da conexão com BD."""
    db = next(get_db())
    try:
        yield db
    finally:
        db.close()

def validate_user_request(user_id: Optional[int], action: str) -> bool:
    """
    Valida se o usuário pode executar a ação solicitada.
    
    Args:
        user_id: ID do usuário
        action: Ação a ser executada
        
    Returns:
        bool: True se válido, False caso contrário
    """
    if not user_id or user_id <= 0:
        logger.warning(f"User ID inválido: {user_id}")
        return False
    
    if action not in CHART_PARAMS and action not in ["grafico_fechar", "grafico_voltar"]:
        logger.warning(f"Ação desconhecida: {action}")
        return False
    
    return True

@lru_cache(maxsize=100)
def get_cached_lancamentos(user_id: int, cache_key: str) -> Optional[List]:
    """
    Cache simples para lançamentos usando LRU cache.
    
    Args:
        user_id: ID do usuário
        cache_key: Chave única para o cache (baseada em timestamp)
        
    Returns:
        Lista de lançamentos ou None
    """
    # O cache real é feito pelo decorator @lru_cache
    # Esta função será chamada apenas quando necessário
    with get_db_context() as db:
        return services.buscar_lancamentos_com_relacionamentos(db, user_id)

def get_cache_key(user_id: int) -> str:
    """
    Gera uma chave de cache baseada no tempo (TTL de 5 minutos).
    
    Args:
        user_id: ID do usuário
        
    Returns:
        Chave de cache única
    """
    now = datetime.now()
    
    # Verifica se precisa invalidar cache do usuário
    if user_id in _cache_timestamps:
        last_update = _cache_timestamps[user_id]
        if now - last_update > timedelta(minutes=CACHE_TTL_MINUTES):
            # Cache expirado, limpa a entrada específica
            get_cached_lancamentos.cache_clear()
            del _cache_timestamps[user_id]
    
    # Atualiza timestamp
    _cache_timestamps[user_id] = now
    
    # Retorna chave baseada em intervalos de 5 minutos
    interval = now.replace(second=0, microsecond=0)
    interval = interval.replace(minute=(interval.minute // CACHE_TTL_MINUTES) * CACHE_TTL_MINUTES)
    
    return f"{user_id}_{interval.isoformat()}"

async def show_chart_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Exibe o menu de gráficos com layout otimizado."""
    keyboard = [
        [
            InlineKeyboardButton("🍕 Desp. por Categoria", callback_data="grafico_categoria_pizza"),
            InlineKeyboardButton("📊 Desp. (Barras)", callback_data="grafico_categoria_barra_h")
        ],
        [
            InlineKeyboardButton("📈 Evolução do Saldo", callback_data="grafico_data_linha"),
            InlineKeyboardButton("🌊 Fluxo de Caixa", callback_data="grafico_fluxo_caixa_area")
        ],
        [
            InlineKeyboardButton("🔮 Projeção de Gastos", callback_data="grafico_projecao_barra_linha"),
            InlineKeyboardButton("💳 Gastos por Pagamento", callback_data="grafico_forma_pagamento_pizza")
        ],
        [InlineKeyboardButton("❌ Fechar", callback_data="grafico_fechar")]
    ]
    
    text = (
        "📊 <b>Painel de Visualização</b>\n"
        "Escolha uma análise para gerar:\n\n"
        "💡 <i>Tip: Os gráficos são gerados com base nos seus lançamentos mais recentes</i>"
    )
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        elif update.callback_query:
            query = update.callback_query
            await query.answer()
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
    except TelegramError as e:
        logger.error(f"Erro ao exibir menu de gráficos: {e}")
        # Fallback para mensagem simples
        simple_text = "📊 Painel de Visualização\nEscolha uma análise para gerar:"
        if update.message:
            await update.message.reply_text(simple_text, reply_markup=reply_markup)
        
    return ChartStates.CHART_MENU

async def chart_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Processa os cliques nos botões do menu de gráficos com tratamento robusto de erros.
    """
    query = update.callback_query
    if not query:
        logger.error("Callback query não encontrada")
        return ChartStates.CHART_MENU
    
    await query.answer()
    action = query.data
    user_id = query.from_user.id
    
    # Validação de entrada
    if not validate_user_request(user_id, action):
        await query.edit_message_text(
            "❌ Solicitação inválida.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
            ]])
        )
        return ChartStates.CHART_MENU

    # Ações de controle
    if action == "grafico_fechar":
        await query.edit_message_text("✅ Painel de gráficos fechado.")
        return ConversationHandler.END
        
    if action == "grafico_voltar":
        return await show_chart_menu(update, context)

    # Processamento de gráficos
    try:
        params = CHART_PARAMS.get(action)
        if not params:
            logger.error(f"Parâmetros não encontrados para ação: {action}")
            await query.edit_message_text(
                "❌ Ação não reconhecida.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
                ]])
            )
            return ChartStates.CHART_MENU

        agrupar_por = params["agrupar_por"]
        tipo_grafico = params["tipo_grafico"]
        
        # Formata o nome para exibição
        nome_exibicao = agrupar_por.replace('_', ' ').title()
        await query.edit_message_text(
            f"⏳ Gerando seu gráfico de <b>{nome_exibicao}</b>...\n"
            f"<i>Isso pode levar alguns segundos...</i>", 
            parse_mode='HTML'
        )
        
        # Busca dados com cache
        cache_key = get_cache_key(user_id)
        lancamentos = get_cached_lancamentos(user_id, cache_key)
        
        if not lancamentos:
            await query.edit_message_text(
                "⚠️ <b>Dados insuficientes</b>\n"
                "Não encontrei lançamentos para gerar este gráfico.\n\n"
                "💡 <i>Adicione alguns lançamentos primeiro!</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
                ]]),
                parse_mode='HTML'
            )
            return ChartStates.CHART_MENU

        # Gera o gráfico
        grafico_buffer = services.gerar_grafico_dinamico(lancamentos, tipo_grafico, agrupar_por)
        
        if grafico_buffer:
            # Envia o gráfico
            await context.bot.send_photo(
                chat_id=query.message.chat.id, 
                photo=grafico_buffer,
                caption=f"📊 <b>{nome_exibicao}</b>\n<i>Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}</i>",
                parse_mode='HTML'
            )
            
            # Retorna ao menu
            return await show_chart_menu(update, context)
        else:
            await query.edit_message_text(
                "⚠️ <b>Erro na geração</b>\n"
                "Não foi possível gerar o gráfico com os dados disponíveis.\n\n"
                "🔍 <i>Verifique se há dados suficientes para esta análise específica.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
                ]]),
                parse_mode='HTML'
            )

    except DatabaseError as e:
        logger.error(f"Erro de banco de dados ao gerar gráfico: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ <b>Erro de conexão</b>\n"
            "Problema temporário com o banco de dados.\n\n"
            "🔄 <i>Tente novamente em alguns instantes.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Tentar Novamente", callback_data="grafico_voltar")
            ]]),
            parse_mode='HTML'
        )
        
    except ServiceError as e:
        logger.error(f"Erro do serviço ao gerar gráfico: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ <b>Erro no processamento</b>\n"
            "Não foi possível processar os dados para o gráfico.\n\n"
            "💡 <i>Verifique se os dados estão em formato válido.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
            ]]),
            parse_mode='HTML'
        )
        
    except TelegramError as e:
        logger.error(f"Erro do Telegram ao enviar gráfico: {e}", exc_info=True)
        # Tenta enviar uma mensagem de erro mais simples
        try:
            await query.edit_message_text(
                "❌ Erro ao enviar o gráfico. Tente novamente.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Tentar Novamente", callback_data="grafico_voltar")
                ]])
            )
        except:
            # Se nem isso funcionar, apenas loga
            logger.error("Falha crítica na comunicação com Telegram")
            
    except Exception as e:
        logger.error(f"Erro inesperado ao gerar gráfico: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                "❌ <b>Erro inesperado</b>\n"
                "Ocorreu um problema interno.\n\n"
                "🛠️ <i>Nossa equipe foi notificada.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("↩️ Voltar ao Menu", callback_data="grafico_voltar")
                ]]),
                parse_mode='HTML'
            )
        except:
            pass
            
    return ChartStates.CHART_MENU

async def cancel_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a operação de gráfico com mensagem amigável."""
    if update.message:
        await update.message.reply_text(
            "✅ <b>Operação cancelada</b>\n"
            "<i>Use /grafico para acessar os gráficos novamente.</i>",
            parse_mode='HTML'
        )
    return ConversationHandler.END

def clear_user_cache(user_id: int) -> None:
    """
    Limpa o cache de um usuário específico.
    Útil quando dados são atualizados.
    
    Args:
        user_id: ID do usuário para limpar cache
    """
    if user_id in _cache_timestamps:
        del _cache_timestamps[user_id]
    
    # Limpa todo o cache LRU (pode ser otimizado para limpar apenas o usuário específico)
    get_cached_lancamentos.cache_clear()
    logger.info(f"Cache limpo para usuário {user_id}")

def get_cache_stats() -> Dict[str, Any]:
    """
    Retorna estatísticas do cache para monitoramento.
    
    Returns:
        Dict com estatísticas do cache
    """
    cache_info = get_cached_lancamentos.cache_info()
    return {
        "hits": cache_info.hits,
        "misses": cache_info.misses,
        "maxsize": cache_info.maxsize,
        "currsize": cache_info.currsize,
        "active_users": len(_cache_timestamps),
        "hit_rate": cache_info.hits / (cache_info.hits + cache_info.misses) if (cache_info.hits + cache_info.misses) > 0 else 0
    }

# ConversationHandler para os gráficos
grafico_conv = ConversationHandler(
    entry_points=[CommandHandler('grafico', show_chart_menu)],
    states={
        ChartStates.CHART_MENU: [
            CallbackQueryHandler(chart_callback_handler, pattern='^grafico_')
        ]
    },
    fallbacks=[CommandHandler('cancelar', cancel_chart)],
    # Adiciona timeout para evitar conversas órfãs
    conversation_timeout=300,  # 5 minutos
    name="grafico_conversation",
    persistent=False
)