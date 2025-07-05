import logging
import requests
import yfinance as yf
import feedparser
from datetime import datetime, timedelta
from collections import defaultdict
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

# --- CACHE ---
cache_indicadores = {"dados": None, "timestamp": datetime.min}


# ===================================================================
# FUNÇÕES SÍNCRONAS (Para APIs que não precisam de alta concorrência)
# ===================================================================

def get_dados_bcb(codigo_bcb: int, dias: int = 1) -> float | None:
    """
    Função genérica e robusta para buscar séries temporais do Banco Central.
    Ex: 11 (Selic Diária), 1178 (Selic Meta), 13522 (IPCA 12m).
    """
    try:
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo_bcb}/dados/ultimos/{dias}?formato=json"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        dados = response.json()
        if dados:
            return float(dados[-1]['valor'])
        return None
    except Exception as e:
        logger.error(f"Erro ao buscar dados do BCB para o código {codigo_bcb}: {e}")
        return None

def get_indicadores_financeiros(use_cache: bool = True) -> dict | None:
    """
    Busca os principais indicadores (Selic Meta e IPCA) usando a função genérica do BCB.
    """
    global cache_indicadores
    if use_cache and cache_indicadores["dados"] and (datetime.now() - cache_indicadores["timestamp"]) < timedelta(hours=1):
        logger.info("Usando indicadores financeiros do cache.")
        return cache_indicadores["dados"]
    
    logger.info("Buscando novos indicadores financeiros no Banco Central...")
    # Usando a função genérica para buscar os códigos
    selic = get_dados_bcb(1178)      # Código para Selic Meta
    ipca_12m = get_dados_bcb(13522)  # Código para IPCA Acumulado 12 meses
    
    if selic is not None and ipca_12m is not None:
        indicadores = {
            "selic_meta_anual": selic, 
            "ipca_acumulado_12m": ipca_12m, 
            "data_consulta": datetime.now().strftime('%d/%m/%Y %H:%M')
        }
        cache_indicadores["dados"] = indicadores
        cache_indicadores["timestamp"] = datetime.now()
        logger.info(f"Indicadores atualizados: {indicadores}")
        return indicadores
        
    logger.error("Falha ao obter um ou mais indicadores do BCB.")
    return None

def get_crypto_price(crypto_symbol: str) -> float | None:
    """
    Obtem o preço de uma criptomoeda em BRL usando a API do CoinGecko.
    (Função consolidada de crypto.py)
    """
    symbol_map = {
        'bitcoin': 'bitcoin', 'btc': 'bitcoin',
        'ethereum': 'ethereum', 'eth': 'ethereum',
    }
    crypto_id = symbol_map.get(crypto_symbol.lower())
    if not crypto_id:
        logger.warning(f"Símbolo de criptomoeda não mapeado: {crypto_symbol}")
        return None

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=brl"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data[crypto_id]["brl"]
    except Exception as e:
        logger.error(f"Erro ao buscar preço da cripto {crypto_id}: {e}")
        return None

def get_info_acao(ticker: str) -> dict | None:
    """Busca informações de uma ação usando a biblioteca yfinance."""
    logger.info(f"Buscando informações para o ticker: {ticker}")
    try:
        if not ticker.upper().endswith('.SA'):
            ticker += '.SA'
        stock = yf.Ticker(ticker)
        info = stock.info
        if not info or 'currentPrice' not in info:
            logger.warning(f"Não foram encontradas informações para o ticker {ticker}.")
            return None
        return {
            "ticker": ticker.upper(), 
            "nome_empresa": info.get('longName'), 
            "preco_atual": info.get('currentPrice'), 
            "variacao_dia": info.get('regularMarketChangePercent', 0) * 100, 
            "min_dia": info.get('dayLow'), 
            "max_dia": info.get('dayHigh'), 
            "dividendo_yield": info.get('dividendYield', 0) * 100
        }
    except Exception as e:
        logger.error(f"Erro ao buscar dados do ticker {ticker} via yfinance: {e}")
        return None

def get_ultimas_noticias_financeiras(n: int = 3) -> list[dict] | None:
    """Busca as últimas notícias de um feed RSS de economia."""
    FEED_URL = "https://g1.globo.com/rss/g1/economia/"
    logger.info(f"Buscando últimas {n} notícias de '{FEED_URL}'...")
    try:
        feed = feedparser.parse(FEED_URL)
        if feed.bozo:
            logger.error(f"Erro ao fazer o parse do feed RSS: {feed.bozo_exception}")
            return None
        noticias = [{"titulo": entry.title, "link": entry.link} for entry in feed.entries[:n]]
        logger.info(f"{len(noticias)} notícias encontradas.")
        return noticias
    except Exception as e:
        logger.error(f"Erro inesperado ao buscar notícias do feed RSS: {e}")
        return None

# ===================================================================
# FUNÇÕES ASSÍNCRONAS (Para APIs que podem ser chamadas em paralelo)
# ===================================================================

async def _fetch_json(session, url):
    """Utilitário genérico para requisições JSON com aiohttp e timeout explícito."""
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()
    except asyncio.TimeoutError:
        logger.error(f"TIMEOUT ERROR: A requisição para a URL {url} demorou mais de 10 segundos.")
        return None
    except aiohttp.ClientResponseError as e:
        logger.error(f"HTTP ERROR: URL {url} retornou erro. Status: {e.status}, Mensagem: {e.message}")
        return None
    except Exception as e:
        logger.error(f"UNEXPECTED ERROR ao buscar a URL {url}: {e}", exc_info=True)
        return None

async def get_exchange_rate(pair="USD/BRL") -> float | None:
    """Obtém a cotação de um par de moedas de forma assíncrona."""
    try:
        code_url = pair.replace('/', '-') 
        url  = f"https://economia.awesomeapi.com.br/last/{code_url}"
        
        async with aiohttp.ClientSession() as session:
            data = await _fetch_json(session, url)
            
            code_json = code_url.replace('-', '')
            return float(data[code_json]["bid"])
            
    except Exception as e:
        logger.error(f"Erro ao obter cotação {pair}: {e}")
        return None

async def get_gas_price() -> float | None:
    """Obtém o preço médio da gasolina de forma assíncrona."""
    # Nota: A API da ANP é instável. Se continuar falhando, pode ser necessário buscar outra fonte.
    logger.warning("A API da ANP está instável. Usando um valor de exemplo para o preço da gasolina.")
    try:
        # TENTATIVA COM UMA API REAL (se encontrar uma que funcione)
        # async with aiohttp.ClientSession() as session:
        #     data = await _fetch_json(session, "URL_DA_API_DE_GASOLINA_FUNCIONAL")
        #     return float(data["..."]) # Ajustar de acordo com o JSON da API
        return None # Retorna None se não houver API funcional
    except Exception as e:
        logger.error(f"Erro ao obter preço da gasolina: {e}")
        return None

async def google_search(query: str, api_key: str, cse_id: str, top: int = 3):
    """Executa uma busca customizada no Google."""
    url = (
        "https://www.googleapis.com/customsearch/v1"
        f"?key={api_key}&cx={cse_id}&q={query}&num={top}"
    )
    async with aiohttp.ClientSession() as s:
        return await _fetch_json(s, url)