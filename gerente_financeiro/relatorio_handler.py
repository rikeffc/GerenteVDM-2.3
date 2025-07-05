# gerente_financeiro/relatorio_handler.py

import logging
from datetime import datetime
from io import BytesIO
import os
from dateutil.relativedelta import relativedelta
import re
import base64

from telegram import Update, InputFile
from telegram.ext import ContextTypes, CommandHandler
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS

from database.database import get_db
from .services import gerar_contexto_relatorio, gerar_grafico_para_relatorio

logger = logging.getLogger(__name__)


# =============================================================================
#  CONFIGURAÇÃO DO AMBIENTE JINJA2 E FILTROS CUSTOMIZADOS
#  (Esta seção deve ser executada apenas uma vez, quando o módulo é importado)
# =============================================================================

def nl2br_filter(s):
    """Filtro Jinja2 para converter quebras de linha em tags <br>."""
    if s is None:
        return ""
    return re.sub(r'\r\n|\r|\n', '<br>\n', str(s))

def color_palette_filter(index):
    """Filtro Jinja2 que retorna uma cor de uma paleta predefinida baseado no índice."""
    colors = ["#3498db", "#e74c3c", "#2ecc71", "#f1c40f", "#9b59b6", "#1abc9c", "#e67e22"]
    return colors[int(index) % len(colors)]

def safe_float_filter(value, default=0.0):
    """Filtro Jinja2 para converter valores para float de forma segura."""
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def safe_format_currency(value):
    """Filtro Jinja2 para formatar valores monetários de forma segura."""
    try:
        return "%.2f" % float(value) if value is not None else "0.00"
    except (ValueError, TypeError):
        return "0.00"

# Define os caminhos para as pastas de templates e arquivos estáticos
templates_path = os.path.join(os.path.dirname(__file__), '..', 'templates')
static_path = os.path.join(os.path.dirname(__file__), '..', 'static')

# Cria e configura o ambiente do Jinja2
env = Environment(
    loader=FileSystemLoader(templates_path),
    autoescape=True  # Ativa o autoescaping para segurança
)

# Adiciona os filtros customizados ao ambiente
env.filters['nl2br'] = nl2br_filter
env.filters['color_palette'] = color_palette_filter
env.filters['safe_float'] = safe_float_filter
env.filters['safe_currency'] = safe_format_currency


# =============================================================================
#  FUNÇÕES AUXILIARES PARA PROCESSAMENTO DE DADOS
# =============================================================================

def validar_e_completar_contexto(contexto_dados):
    """Valida e completa o contexto de dados para garantir que todos os campos necessários existam."""
    
    # Campos obrigatórios com valores padrão
    campos_padrao = {
        'mes_nome': 'Mês Atual',
        'ano': datetime.now().year,
        'receita_total': 0.0,
        'despesa_total': 0.0,
        'saldo_mes': 0.0,
        'taxa_poupanca': 0.0,
        'gastos_agrupados': [],
        'gastos_por_categoria_dict': {},
        'metas': [],
        'analise_ia': None,
        'has_data': False
    }
    
    # Aplica valores padrão para campos ausentes
    for campo, valor_padrao in campos_padrao.items():
        if campo not in contexto_dados or contexto_dados[campo] is None:
            contexto_dados[campo] = valor_padrao
    
    # Garante que valores numéricos sejam float
    campos_numericos = ['receita_total', 'despesa_total', 'saldo_mes', 'taxa_poupanca']
    for campo in campos_numericos:
        try:
            contexto_dados[campo] = float(contexto_dados[campo])
        except (ValueError, TypeError):
            contexto_dados[campo] = 0.0
    
    # Processa metas para garantir campos necessários
    if contexto_dados['metas']:
        for meta in contexto_dados['metas']:
            # Garante que todos os campos da meta existam
            meta_campos_padrao = {
                'descricao': 'Meta sem descrição',
                'valor_atual': 0.0,
                'valor_meta': 0.0,
                'progresso_percent': 0.0
            }
            
            for campo, valor_padrao in meta_campos_padrao.items():
                if campo not in meta or meta[campo] is None:
                    meta[campo] = valor_padrao
            
            # Converte valores numéricos
            try:
                meta['valor_atual'] = float(meta['valor_atual'])
                meta['valor_meta'] = float(meta['valor_meta'])
                
                # Calcula progresso se não estiver definido
                if meta['valor_meta'] > 0:
                    meta['progresso_percent'] = (meta['valor_atual'] / meta['valor_meta']) * 100
                else:
                    meta['progresso_percent'] = 0.0
                
                # Cria campo para display da barra de progresso (limitado a 100%)
                meta['progresso_percent_display'] = min(meta['progresso_percent'], 100.0)
                
            except (ValueError, TypeError):
                meta['valor_atual'] = 0.0
                meta['valor_meta'] = 0.0
                meta['progresso_percent'] = 0.0
                meta['progresso_percent_display'] = 0.0
    
    # Garante que usuario existe
    if 'usuario' not in contexto_dados or not contexto_dados['usuario']:
        class UsuarioMock:
            nome_completo = "Usuário"
        contexto_dados['usuario'] = UsuarioMock()
    
    return contexto_dados

def debug_contexto(contexto_dados):
    """Função para debug - registra informações do contexto no log."""
    logger.info("=== DEBUG CONTEXTO RELATÓRIO ===")
    logger.info(f"Has data: {contexto_dados.get('has_data', False)}")
    logger.info(f"Mês/Ano: {contexto_dados.get('mes_nome', 'N/A')} {contexto_dados.get('ano', 'N/A')}")
    logger.info(f"Receita: R$ {contexto_dados.get('receita_total', 0):.2f}")
    logger.info(f"Despesa: R$ {contexto_dados.get('despesa_total', 0):.2f}")
    logger.info(f"Saldo: R$ {contexto_dados.get('saldo_mes', 0):.2f}")
    logger.info(f"Taxa poupança: {contexto_dados.get('taxa_poupanca', 0):.1f}%")
    logger.info(f"Categorias: {len(contexto_dados.get('gastos_agrupados', []))}")
    logger.info(f"Metas: {len(contexto_dados.get('metas', []))}")
    logger.info(f"Análise IA: {'Sim' if contexto_dados.get('analise_ia') else 'Não'}")
    logger.info(f"Gráfico: {'Sim' if contexto_dados.get('grafico_pizza_base64') else 'Não'}")
    logger.info("===============================")


# =============================================================================
#  HANDLER DO COMANDO /relatorio
# =============================================================================

async def gerar_relatorio_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera e envia um relatório financeiro detalhado em PDF."""
    
    hoje = datetime.now()
    
    # Determina o período do relatório (mês atual ou passado)
    if context.args and context.args[0].lower() in ['passado', 'anterior']:
        data_alvo = hoje - relativedelta(months=1)
        periodo_str = f"do mês passado ({data_alvo.strftime('%B de %Y')})"
    else:
        data_alvo = hoje
        periodo_str = "deste mês"
        
    mes_alvo = data_alvo.month
    ano_alvo = data_alvo.year

    await update.message.reply_text(f"Gerando seu relatório {periodo_str}... 🎥\nIsso pode levar alguns segundos.")
    
    db = next(get_db())
    user_id = update.effective_user.id
    
    try:
        # 1. Obter todos os dados necessários do backend
        logger.info(f"Iniciando geração de relatório para usuário {user_id}, mês {mes_alvo}, ano {ano_alvo}")
        contexto_dados = gerar_contexto_relatorio(db, user_id, mes_alvo, ano_alvo)
        
        if not contexto_dados:
            await update.message.reply_text("Não foi possível encontrar seu usuário. Tente usar o bot uma vez para se registrar.")
            return
        
        # 2. Validar e completar contexto
        contexto_dados = validar_e_completar_contexto(contexto_dados)
        
        # 3. Debug do contexto (pode ser removido em produção)
        debug_contexto(contexto_dados)
        
        if not contexto_dados.get("has_data"):
            await update.message.reply_text(f"Não encontrei dados suficientes para {periodo_str} para gerar um relatório.")
            return

        # 4. Gerar o gráfico de pizza dinamicamente
        logger.info("Gerando gráfico de pizza...")
        try:
            grafico_buffer = gerar_grafico_para_relatorio(contexto_dados.get("gastos_por_categoria_dict", {}))
            
            if grafico_buffer:
                grafico_base64 = base64.b64encode(grafico_buffer.getvalue()).decode('utf-8')
                contexto_dados["grafico_pizza_base64"] = grafico_base64
                logger.info("Gráfico gerado com sucesso")
            else:
                contexto_dados["grafico_pizza_base64"] = None
                logger.warning("Falha ao gerar gráfico")
        except Exception as e:
            logger.error(f"Erro ao gerar gráfico: {e}")
            contexto_dados["grafico_pizza_base64"] = None
        
        # 5. Renderizar o template HTML com os dados
        logger.info("Renderizando template HTML...")
        try:
            template = env.get_template('relatorio.html')
            html_renderizado = template.render(contexto_dados)
            logger.info(f"Template renderizado. Tamanho: {len(html_renderizado)} caracteres")
            
            # Debug: salva HTML temporariamente para verificação (apenas em desenvolvimento)
            # Descomente as linhas abaixo se precisar verificar o HTML gerado
            # with open(f"debug_relatorio_{user_id}.html", "w", encoding="utf-8") as f:
            #     f.write(html_renderizado)
            # logger.info("HTML de debug salvo")
            
        except Exception as e:
            logger.error(f"Erro ao renderizar template: {e}", exc_info=True)
            raise
        
        # 6. Carregar o CSS e gerar o PDF
        logger.info("Gerando PDF...")
        try:
            caminho_css = os.path.join(static_path, 'relatorio.css')
            
            # Verifica se o arquivo CSS existe
            if not os.path.exists(caminho_css):
                logger.warning(f"Arquivo CSS não encontrado: {caminho_css}")
                # Gera PDF sem CSS se necessário
                pdf_bytes = HTML(string=html_renderizado, base_url=static_path).write_pdf()
            else:
                css = CSS(caminho_css)
                pdf_bytes = HTML(string=html_renderizado, base_url=static_path).write_pdf(stylesheets=[css])
            
            logger.info(f"PDF gerado. Tamanho: {len(pdf_bytes)} bytes")
            
        except Exception as e:
            logger.error(f"Erro ao gerar PDF: {e}", exc_info=True)
            raise
        
        # 7. Preparar e enviar o arquivo PDF para o usuário
        logger.info("Enviando PDF...")
        try:
            pdf_buffer = BytesIO(pdf_bytes)
            nome_usuario_safe = contexto_dados['usuario'].nome_completo.split(' ')[0] if hasattr(contexto_dados.get('usuario'), 'nome_completo') else "Usuario"
            # Remove caracteres especiais do nome para o arquivo
            nome_usuario_safe = re.sub(r'[^\w\-_]', '', nome_usuario_safe)
            pdf_buffer.name = f"Relatorio_{data_alvo.strftime('%Y-%m')}_{nome_usuario_safe}.pdf"
            
            await context.bot.send_document(
                chat_id=user_id,
                document=InputFile(pdf_buffer),
                caption=f"✅ Aqui está o seu relatório financeiro {periodo_str}!"
            )
            
            logger.info(f"Relatório enviado com sucesso para usuário {user_id}")
            
        except Exception as e:
            logger.error(f"Erro ao enviar PDF: {e}", exc_info=True)
            raise

    except Exception as e:
        logger.error(f"Erro geral ao gerar relatório para o usuário {user_id}: {e}", exc_info=True)
        await update.message.reply_text("❌ Ops! Ocorreu um erro ao gerar seu relatório. A equipe de filmagem já foi notificada.")
    finally:
        db.close()
        

# Cria o handler para ser importado no bot.py
relatorio_handler = CommandHandler('relatorio', gerar_relatorio_comando)