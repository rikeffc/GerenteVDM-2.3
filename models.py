# models.py
from datetime import datetime, timezone, time
from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, ForeignKey, BigInteger, Boolean, Date, Time
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()

class Usuario(Base):
    __tablename__ = 'usuarios'
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    nome_completo = Column(String, nullable=True)
    perfil_investidor = Column(String, nullable=True)
    horario_notificacao = Column(Time, default=time(hour=9, minute=0))
    
    # --- NOVA COLUNA PARA O "GUARDIÃO DE METAS" ---
    alerta_gastos_ativo = Column(Boolean, default=True)
    
    criado_em = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    lancamentos = relationship("Lancamento", back_populates="usuario", cascade="all, delete-orphan")
    contas = relationship("Conta", back_populates="usuario", cascade="all, delete-orphan")
    objetivos = relationship("Objetivo", back_populates="usuario", cascade="all, delete-orphan")
    agendamentos = relationship("Agendamento", back_populates="usuario", cascade="all, delete-orphan")

class Objetivo(Base):
    __tablename__ = 'objetivos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_usuario = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    descricao = Column(String, nullable=False)
    valor_meta = Column(Numeric(12, 2), nullable=False)
    valor_atual = Column(Numeric(12, 2), default=0.0)
    data_meta = Column(Date, nullable=True)
    criado_em = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    usuario = relationship("Usuario", back_populates="objetivos")

# --- TABELA DE CONTAS REFORMULADA ---
class Conta(Base):
    __tablename__ = 'contas'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_usuario = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    nome = Column(String, nullable=False) # Ex: "Nubank", "Inter Gold"
    tipo = Column(String, nullable=False) # "Conta Corrente", "Cartão de Crédito", "Carteira Digital", "Outro"
    
    # Campos específicos para Cartão de Crédito
    dia_fechamento = Column(Integer, nullable=True)
    dia_vencimento = Column(Integer, nullable=True)
    
    usuario = relationship("Usuario", back_populates="contas")
    lancamentos = relationship("Lancamento", back_populates="conta")

class Categoria(Base):
    __tablename__ = 'categorias'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, unique=True, nullable=False)
    
    subcategorias = relationship("Subcategoria", back_populates="categoria", cascade="all, delete-orphan")
    lancamentos = relationship("Lancamento", back_populates="categoria")

class Subcategoria(Base):
    __tablename__ = 'subcategorias'
    id = Column(Integer, primary_key=True, autoincrement=True)
    nome = Column(String, nullable=False)
    id_categoria = Column(Integer, ForeignKey('categorias.id'), nullable=False)
    
    categoria = relationship("Categoria", back_populates="subcategorias")
    lancamentos = relationship("Lancamento", back_populates="subcategoria")

class Lancamento(Base):
    __tablename__ = 'lancamentos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    descricao = Column(String)
    valor = Column(Numeric(10, 2), nullable=False)
    tipo = Column(String, nullable=False)
    data_transacao = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    forma_pagamento = Column(String) # Será preenchido com o nome da conta/cartão
    documento_fiscal = Column(String, nullable=True)
    
    id_usuario = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    id_conta = Column(Integer, ForeignKey('contas.id'), nullable=True) # Link para a conta/cartão usado
    id_categoria = Column(Integer, ForeignKey('categorias.id'), nullable=True)
    id_subcategoria = Column(Integer, ForeignKey('subcategorias.id'), nullable=True)
    
    usuario = relationship("Usuario", back_populates="lancamentos")
    conta = relationship("Conta", back_populates="lancamentos")
    categoria = relationship("Categoria", back_populates="lancamentos")
    subcategoria = relationship("Subcategoria", back_populates="lancamentos")
    itens = relationship("ItemLancamento", back_populates="lancamento", cascade="all, delete-orphan")

class ItemLancamento(Base):
    __tablename__ = 'itens_lancamento'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_lancamento = Column(Integer, ForeignKey('lancamentos.id'), nullable=False)
    nome_item = Column(String, nullable=False)
    quantidade = Column(Numeric(10, 3))
    valor_unitario = Column(Numeric(10, 2))
    
    lancamento = relationship("Lancamento", back_populates="itens")

class Agendamento(Base):
    __tablename__ = 'agendamentos'
    id = Column(Integer, primary_key=True, autoincrement=True)
    id_usuario = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    descricao = Column(String, nullable=False)
    valor = Column(Numeric(12, 2), nullable=False)
    tipo = Column(String, nullable=False)
    
    id_categoria = Column(Integer, ForeignKey('categorias.id'), nullable=True)
    id_subcategoria = Column(Integer, ForeignKey('subcategorias.id'), nullable=True)
    
    data_primeiro_evento = Column(Date, nullable=False)
    frequencia = Column(String, nullable=False)
    
    total_parcelas = Column(Integer, nullable=True)
    parcela_atual = Column(Integer, default=0)
    
    proxima_data_execucao = Column(Date, nullable=False, index=True)
    ativo = Column(Boolean, default=True, index=True)
    
    criado_em = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    usuario = relationship("Usuario", back_populates="agendamentos")
    categoria = relationship("Categoria")
    subcategoria = relationship("Subcategoria")