# Arquivo: config.py

import os
from dotenv import load_dotenv
load_dotenv()
import logging

# --- CARREGAMENTO EXPLÍCITO DO .ENV ---

# Pega o caminho absoluto para o diretório onde este arquivo (config.py) está
basedir = os.path.abspath(os.path.dirname(__file__))

# Constrói o caminho completo para o arquivo .env na pasta raiz do projeto
# (assumindo que config.py está na raiz ou em uma subpasta)
# Se config.py está na raiz, o caminho será /caminho/para/projeto/.env
# Se config.py está em /gerente_financeiro, precisamos voltar um nível:
# dotenv_path = os.path.join(os.path.dirname(basedir), '.env')
# Para sua estrutura, o .env está na raiz, então o seguinte é mais simples:
dotenv_path = os.path.join(basedir, '.env')

# Verifica se o arquivo .env existe no caminho esperado
if os.path.exists(dotenv_path):
    logging.info(f"Carregando variáveis de ambiente de: {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
else:
    logging.warning(f"AVISO: Arquivo .env não encontrado em {dotenv_path}. O programa dependerá de variáveis de ambiente do sistema.")


# --- CARREGAMENTO DAS VARIÁVEIS DE AMBIENTE ---

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-flash")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# ----- ADICIONANDO VARIÁVEL DE CHAVE PIX E CONTATO -----
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")
PIX_KEY = os.getenv("PIX_KEY")


# --- VALIDAÇÃO E CONFIGURAÇÃO ADICIONAL ---

required_vars = {
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "GEMINI_API_KEY": GEMINI_API_KEY,
    "DATABASE_URL": DATABASE_URL,
    "GOOGLE_APPLICATION_CREDENTIALS": GOOGLE_APPLICATION_CREDENTIALS,
}

missing_vars = [key for key, value in required_vars.items() if not value]
if missing_vars:
    raise ValueError(f"As seguintes variáveis de ambiente essenciais não foram definidas no arquivo .env ou no sistema: {', '.join(missing_vars)}")

if GOOGLE_APPLICATION_CREDENTIALS:
    if os.path.exists(GOOGLE_APPLICATION_CREDENTIALS):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS
        logging.info("✅ Credenciais do Google Application encontradas e configuradas com sucesso.")
    else:
        raise FileNotFoundError(f"ERRO CRÍTICO: O arquivo de credenciais do Google não foi encontrado no caminho especificado em .env: {GOOGLE_APPLICATION_CREDENTIALS}")
else:
    logging.warning("AVISO: A variável de ambiente GOOGLE_APPLICATION_CREDENTIALS não foi definida.")