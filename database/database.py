# database/database.py
import logging
from typing import List
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from models import Base, Lancamento, Usuario, Categoria, Subcategoria, Objetivo
from datetime import datetime
import config
from sqlalchemy.orm import joinedload
from sqlalchemy import func, and_
from models import Lancamento, Usuario, Categoria, Subcategoria, Objetivo, ItemLancamento

class DatabaseError(Exception):
    """Exceção personalizada para erros de banco de dados."""
    pass

class ServiceError(Exception):
    """Exceção personalizada para erros de serviço interno (regra de negócio, processamento, etc)."""
    pass

# --- Configuração da Conexão com SQLAlchemy ---
engine = None
SessionLocal = None

try:
    if not config.DATABASE_URL:
        raise ValueError("DATABASE_URL não configurada em config.py")

    engine = create_engine(config.DATABASE_URL, client_encoding='utf8')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    with engine.connect() as connection:
        logging.info("✅ Conexão com o banco de dados estabelecida com sucesso!")

except Exception as e:
    logging.critical(f"❌ ERRO CRÍTICO AO CONFIGURAR O BANCO DE DADOS: {e}")
    engine = None


def deletar_todos_dados_usuario(telegram_id: int) -> bool:
    """
    Encontra um usuário pelo seu telegram_id e deleta o registro dele.
    Devido ao cascade, todos os dados associados (lançamentos, metas, etc.)
    serão deletados automaticamente.
    """
    db = next(get_db())
    try:
        # Encontra o usuário para garantir que ele exista
        usuario_a_deletar = db.query(Usuario).filter(Usuario.telegram_id == telegram_id).first()
        
        if usuario_a_deletar:
            # A mágica acontece aqui!
            db.delete(usuario_a_deletar)
            db.commit()
            logging.info(f"Todos os dados do usuário com telegram_id {telegram_id} foram deletados com sucesso.")
            return True
        else:
            logging.warning(f"Tentativa de deletar dados de um usuário inexistente: {telegram_id}")
            return False
            
    except Exception as e:
        db.rollback()
        logging.error(f"Erro CRÍTICO ao deletar dados do usuário {telegram_id}: {e}", exc_info=True)
        return False
    finally:
        db.close()    

# --- Funções Auxiliares ---
def criar_tabelas():
    if not engine:
        logging.error("Engine do banco de dados não inicializada. Tabelas não podem ser criadas.")
        return
    try:
        logging.info("Verificando e criando tabelas a partir dos modelos...")
        Base.metadata.create_all(bind=engine)
        logging.info("Tabelas prontas.")
    except Exception as e:
        logging.error(f"Erro ao criar tabelas: {e}")

def get_db():
    """Fornece uma sessão do banco de dados."""
    if not SessionLocal:
        logging.error("A sessão do banco de dados não foi inicializada.")
        raise ConnectionError("A conexão com o banco de dados falhou na inicialização.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_or_create_user(db_session: Session, telegram_id: int, full_name: str) -> Usuario:
    """Busca um usuário pelo telegram_id ou cria um novo se não existir."""
    user = db_session.query(Usuario).filter(Usuario.telegram_id == telegram_id).first()
    if not user:
        logging.info(f"Criando novo usuário para telegram_id: {telegram_id}")
        user = Usuario(telegram_id=telegram_id, nome_completo=full_name)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
    return user

def popular_dados_iniciais(db_session: Session):
    """
    Verifica e popula o banco com categorias e subcategorias padrão,
    garantindo que não haja duplicatas e adicionando novas se necessário.
    """
    logging.info("Verificando e populando categorias e subcategorias padrão...")

    # Dicionário de categorias e suas subcategorias padrão
    categorias_padrao = {
        "Moradia": ["Aluguel", "Condomínio", "Contas (Luz, Água, Gás)", "Manutenção/Reforma"],
        "Alimentação": ["Supermercado", "Restaurante/Delivery"],
        "Transporte": ["Combustível", "App de Transporte", "Transporte Público", "Manutenção Veicular"],
        "Saúde": ["Farmácia", "Consulta Médica", "Plano de Saúde"],
        "Lazer": ["Cinema/Streaming", "Viagens", "Hobbies", "Eventos/Shows"],
        "Educação": ["Cursos", "Livros/Material"],
        "Serviços": ["Assinaturas (Internet, Celular)", "Serviços Profissionais"],
        "Compras": ["Roupas e Acessórios", "Eletrônicos", "Casa e Decoração"],
        "Receitas": ["Salário", "Freelance", "Vendas", "Rendimentos", "Outras Receitas"],
        "Investimentos": ["Aporte", "Resgate"],
        # --- CATEGORIAS ESPECIAIS PARA ANÁLISE AUTOMÁTICA ---
        "Transferência": ["Entre Contas", "PIX Enviado", "PIX Recebido"],
        "Financeiro": ["Juros", "Taxas Bancárias", "Empréstimos"],
        "Outros": ["Presentes", "Doações", "Despesas não categorizadas"]
    }

    # (O resto da função continua exatamente igual)
    for nome_cat, subs in categorias_padrao.items():
        categoria_obj = db_session.query(Categoria).filter(func.lower(Categoria.nome) == func.lower(nome_cat)).first()
        if not categoria_obj:
            categoria_obj = Categoria(nome=nome_cat)
            db_session.add(categoria_obj)
            db_session.commit()
            db_session.refresh(categoria_obj)
            logging.info(f"Categoria '{nome_cat}' criada.")

        for nome_sub in subs:
            subcategoria_obj = db_session.query(Subcategoria).filter(
                and_(Subcategoria.id_categoria == categoria_obj.id, func.lower(Subcategoria.nome) == func.lower(nome_sub))
            ).first()
            if not subcategoria_obj:
                nova_sub = Subcategoria(nome=nome_sub, id_categoria=categoria_obj.id)
                db_session.add(nova_sub)
                logging.info(f"Subcategoria '{nome_sub}' criada para '{nome_cat}'.")

    db_session.commit()
    logging.info("Verificação de dados iniciais concluída.")
    

def criar_novo_objetivo(telegram_user_id: int, descricao: str, valor_meta: float, data_final: datetime.date) -> Objetivo | str | None:
    db = next(get_db())
    try:
        usuario = db.query(Usuario).filter(Usuario.telegram_id == telegram_user_id).first()
        if not usuario:
            logging.error(f"Usuário com telegram_id {telegram_user_id} não encontrado para criar objetivo.")
            return None
        meta_existente = db.query(Objetivo).filter(
            Objetivo.id_usuario == usuario.id,
            func.lower(Objetivo.descricao) == func.lower(descricao)
        ).first()
        if meta_existente:
            logging.warning(f"Tentativa de criar meta duplicada: '{descricao}' para o usuário {telegram_user_id}")
            return "DUPLICATE"
        novo_objetivo = Objetivo(
            id_usuario=usuario.id,
            descricao=descricao,
            valor_meta=valor_meta,
            data_meta=data_final,
            valor_atual=0.0
        )
        db.add(novo_objetivo)
        db.commit()
        db.refresh(novo_objetivo)
        logging.info(f"Novo objetivo '{descricao}' criado para o usuário {telegram_user_id}.")
        return novo_objetivo
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao criar objetivo no DB: {e}", exc_info=True)
        return None
    finally:
        db.close()

def listar_objetivos_usuario(telegram_user_id: int):
    db = next(get_db())
    try:
        usuario = db.query(Usuario).filter(Usuario.telegram_id == telegram_user_id).first()
        if not usuario:
            return []
        return db.query(Objetivo).filter(Objetivo.id_usuario == usuario.id).order_by(Objetivo.data_meta.asc()).all()
    finally:
        db.close()

def deletar_objetivo_por_id(objetivo_id: int, telegram_user_id: int) -> bool:
    db = next(get_db())
    try:
        objetivo_para_deletar = db.query(Objetivo).join(Usuario).filter(
            Objetivo.id == objetivo_id,
            Usuario.telegram_id == telegram_user_id
        ).first()
        if objetivo_para_deletar:
            db.delete(objetivo_para_deletar)
            db.commit()
            logging.info(f"Objetivo {objetivo_id} deletado com sucesso pelo usuário {telegram_user_id}.")
            return True
        else:
            logging.warning(f"Falha ao deletar objetivo {objetivo_id}. Motivo: Não encontrado ou permissão negada para o usuário {telegram_user_id}.")
            return False
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao deletar objetivo {objetivo_id} no DB: {e}", exc_info=True)
        return False
    finally:
        db.close()

# --- FUNÇÕES ADICIONADAS PARA OS ALERTAS ---

def listar_todos_objetivos_ativos():
    """Busca todos os objetivos de todos os usuários que ainda estão ativos."""
    db = next(get_db())
    try:
        return db.query(Objetivo).join(Usuario).filter(Objetivo.data_meta >= datetime.now().date()).all()
    except Exception as e:
        logging.error(f"Erro ao listar todos os objetivos ativos: {e}", exc_info=True)
        return []
    finally:
        db.close()

def atualizar_valor_objetivo(objetivo_id: int, novo_valor: float):
    """Atualiza o valor atual de um objetivo."""
    db = next(get_db())
    try:
        objetivo = db.query(Objetivo).filter(Objetivo.id == objetivo_id).first()
        if objetivo:
            objetivo.valor_atual = novo_valor
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao atualizar valor do objetivo {objetivo_id}: {e}", exc_info=True)
        return False
    finally:
        db.close()

def atualizar_objetivo_por_id(objetivo_id: int, telegram_user_id: int, novo_valor: float, nova_data: datetime.date) -> Objetivo | None:
    """Atualiza o valor e a data de uma meta específica."""
    db = next(get_db())
    try:
        # Garante que o usuário só pode editar suas próprias metas
        objetivo_para_atualizar = db.query(Objetivo).join(Usuario).filter(
            Objetivo.id == objetivo_id,
            Usuario.telegram_id == telegram_user_id
        ).first()

        if objetivo_para_atualizar:
            objetivo_para_atualizar.valor_meta = novo_valor
            objetivo_para_atualizar.data_meta = nova_data
            db.commit()
            db.refresh(objetivo_para_atualizar)
            logging.info(f"Objetivo {objetivo_id} atualizado com sucesso pelo usuário {telegram_user_id}.")
            return objetivo_para_atualizar
        else:
            logging.warning(f"Falha ao atualizar objetivo {objetivo_id}. Motivo: Não encontrado ou permissão negada para o usuário {telegram_user_id}.")
            return None
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao atualizar objetivo {objetivo_id} no DB: {e}", exc_info=True)
        return None
    finally:
        db.close()

def buscar_lancamentos_usuario(
    telegram_user_id: int,
    limit: int = 10,
    query: str = None,
    lancamento_id: int = None,
    categoria_nome: str = None,
    data_inicio: datetime = None,
    data_fim: datetime = None,
    tipo: str = None,
    id_conta: int = None,
    forma_pagamento: str = None
) -> List[Lancamento]:
    """
    Busca lançamentos para um usuário, com filtros avançados.
    """
    db = next(get_db())
    try:
        # Busca o usuário para garantir que ele existe
        usuario = db.query(Usuario).filter(Usuario.telegram_id == telegram_user_id).first()
        if not usuario:
            return []

        # Inicia a query base, já otimizando para carregar os relacionamentos
        base_query = db.query(Lancamento).filter(Lancamento.id_usuario == usuario.id).options(
            joinedload(Lancamento.categoria),
            joinedload(Lancamento.subcategoria),
            joinedload(Lancamento.itens)
        )

        # --- APLICAÇÃO CORRETA E INDEPENDENTE DOS FILTROS ---

        # Filtro 1: Por tipo ('Entrada' ou 'Saída')
        if tipo:
            base_query = base_query.filter(Lancamento.tipo == tipo)

        # Filtro 2: Por ID específico do lançamento
        if lancamento_id:
            base_query = base_query.filter(Lancamento.id == lancamento_id)

        # Filtro 3: Por texto de busca (na descrição ou nos itens)
        if query:
            base_query = base_query.outerjoin(Lancamento.itens).filter(
                (Lancamento.descricao.ilike(f'%{query}%')) |
                (Lancamento.itens.any(ItemLancamento.nome_item.ilike(f'%{query}%')))
            )

        # Filtro 4: Por nome da categoria
        if categoria_nome:
            base_query = base_query.join(Lancamento.categoria).filter(
                Categoria.nome.ilike(f'%{categoria_nome}%')
            )

        # Filtro 5: Por data de início
        if data_inicio:
            base_query = base_query.filter(Lancamento.data_transacao >= data_inicio)

        # Filtro 6: Por data de fim
        if data_fim:
            base_query = base_query.filter(Lancamento.data_transacao <= data_fim)

        # Filtro 7: Por ID da conta (se necessário)
        if id_conta:
            base_query = base_query.filter(Lancamento.id_conta == id_conta)

        if id_conta:
            base_query = base_query.filter(Lancamento.id_conta == id_conta)

        if forma_pagamento:
            # Usamos ilike para ser case-insensitive (não importa se é 'pix' ou 'PIX')
            base_query = base_query.filter(Lancamento.forma_pagamento.ilike(f'%{forma_pagamento}%'))        

        # Retorna o resultado final, ordenado por data e com limite aplicado.
        # O .distinct() é crucial para evitar duplicatas quando há join com os itens.
        return base_query.distinct().order_by(Lancamento.data_transacao.desc()).limit(limit).all()

    except Exception as e:
        logging.error(f"Erro ao buscar lançamentos no banco de dados: {e}", exc_info=True)
        return []
    finally:
        db.close()

def atualizar_lancamento_por_id(lancamento_id: int, telegram_user_id: int, dados: dict):
    """Atualiza um lançamento específico, verificando a permissão do usuário."""
    db = next(get_db())
    try:
        lancamento = db.query(Lancamento).join(Usuario).filter(
            Lancamento.id == lancamento_id,
            Usuario.telegram_id == telegram_user_id
        ).first()
        
        if lancamento:
            for key, value in dados.items():
                setattr(lancamento, key, value)
            db.commit()
            return lancamento
        return None
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao atualizar lançamento {lancamento_id}: {e}", exc_info=True)
        return None
    finally:
        db.close()

def deletar_lancamento_por_id(lancamento_id: int, telegram_user_id: int) -> bool:
    """Deleta um lançamento específico, verificando a permissão do usuário."""
    db = next(get_db())
    try:
        lancamento = db.query(Lancamento).join(Usuario).filter(
            Lancamento.id == lancamento_id,
            Usuario.telegram_id == telegram_user_id
        ).first()
        
        if lancamento:
            db.delete(lancamento)
            db.commit()
            return True
        return False
    except Exception as e:
        db.rollback()
        logging.error(f"Erro ao deletar lançamento {lancamento_id}: {e}", exc_info=True)
        return False
    finally:
        db.close()