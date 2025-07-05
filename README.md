# Maestro Financeiro 🎼

Seu Assistente Pessoal de Finanças no Telegram, com Inteligência Artificial.

![Python](https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)
![Code Style](https://img.shields.io/badge/code%20style-black-000000.svg?style=for-the-badge)

---

## ✨ Visão Geral

O Maestro Financeiro é um bot de Telegram para controle de finanças pessoais, combinando IA Generativa (Google Gemini) e OCR (Google Vision) para automação, análise e insights financeiros.

---

## 🎬 Demonstração

> Substitua o GIF abaixo por um da sua aplicação real!

![Demonstração](https://media3.giphy.com/media/v1.Y2lkPTc5MGI3NjExZG54NWFhcXBndmV1eGNkdnY4aDdxdjRxMjhjbTZiaTJpNmJnYmF6eSZlcD12MV9pbnRlcm5hbF9naWZfYnlfaWQmY3Q9Zw/L1FJH5e3Q0o7O6tT3g/giphy.gif)

---

## 🚀 Funcionalidades

- **📸 OCR Inteligente:** Envie fotos de cupons/recibos, o bot extrai e categoriza automaticamente.
- **🧠 IA Conversacional:** Pergunte em linguagem natural, obtenha análises e relatórios.
- **📊 Gráficos Dinâmicos:** Gere gráficos de despesas, receitas e projeções.
- **🌐 Dados de Mercado:** Consulte cotações, taxas e indicadores econômicos em tempo real.
- **👤 Perfil de Investidor:** Receba dicas personalizadas conforme seu perfil.
- **✍️ Gestão Completa:** Lançamento manual, agendamento de alertas, onboarding amigável.

---

## 🛠️ Tecnologias

| Área                | Tecnologias/Bibliotecas                                      |
|---------------------|-------------------------------------------------------------|
| Backend             | Python 3.11+                                                |
| IA & ML             | Google Gemini Pro, Google Vision API, OpenCV, Pandas, Matplotlib |
| Banco de Dados      | PostgreSQL, SQLAlchemy                                      |
| APIs & Bot          | python-telegram-bot, aiohttp                                |
| Infraestrutura      | Docker (sugerido), Railway/Heroku/Oracle Cloud              |

---

## 🏛️ Arquitetura

- **Modular:** Separação clara entre manipuladores (handlers), serviços, modelos e utilitários.
- **ORM:** Modelos de dados com SQLAlchemy.
- **Assíncrono:** Uso de async/await e aiohttp para alta performance.
- **Escalável:** Fácil de manter e expandir.

---

## 📂 Estrutura do Projeto

```
.
├── bot.py                  # Inicialização e configuração do bot
├── config.py               # Configurações e variáveis de ambiente
├── models.py               # Modelos ORM (SQLAlchemy)
├── alerts.py               # Alertas e agendamentos
├── analytics.py            # Análises e relatórios
├── database/
│   └── database.py         # Conexão e operações com o banco de dados
├── gerente_financeiro/
│   ├── handlers.py         # Manipuladores de comandos e conversas
│   ├── services.py         # Lógica de negócio
│   ├── prompts.py          # Prompts para IA
│   └── ...                 # Outros módulos auxiliares
├── credenciais/            # Chaves e credenciais (NÃO versionar!)
├── requirements.txt        # Dependências do projeto
└── README.md               # Este arquivo
```

---

## ⚡ Instalação e Execução

### Pré-requisitos

- Python 3.11+
- PostgreSQL
- Contas e chaves de API:
  - Telegram
  - Google Cloud (Gemini e Vision)
  - (Opcional) Pesquisa personalizada do Google

### Passos

1. **Clone o repositório**
    ```sh
    git clone https://github.com/seu-usuario/maestro-financeiro.git
    cd maestro-financeiro
    ```

2. **Crie e ative o ambiente virtual**
    ```sh
    # Windows
    python -m venv venv
    .\venv\Scripts\activate

    # macOS/Linux
    python3 -m venv venv
    source venv/bin/activate
    ```

3. **Instale as dependências**
    ```sh
    pip install -r requirements.txt
    ```

4. **Configure as variáveis de ambiente**
    - Copie `.env.example` para `.env` e preencha com suas chaves e URLs.

5. **Execute o bot**
    ```sh
    python bot.py
    ```

---

## 🛡️ Segurança

- **NUNCA** compartilhe suas credenciais ou arquivos da pasta `credenciais/`.
- Use variáveis de ambiente para todas as chaves sensíveis.

---

## 📄 Licença

Este projeto é protegido por direitos autorais e fornecido apenas para fins de demonstração e portfólio. É proibida a cópia, redistribuição, modificação, uso comercial ou publicação deste código, total ou parcial, sem autorização expressa e por escrito do autor. Para permissões especiais, entre em contato.

---

## 📫 Contato

Dúvidas, sugestões ou bugs? Abra uma issue ou envie um e-mail para [seu-email@dominio.com](mailto:seu-email@dominio.com).
