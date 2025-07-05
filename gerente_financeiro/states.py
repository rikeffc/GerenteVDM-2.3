# Arquivo central para definir todos os estados de conversa
# Isso evita importações circulares.

# Estados do fluxo de Lançamento (Manual e OCR)
(
    AWAITING_LAUNCH_ACTION,
    ASK_DESCRIPTION, ASK_VALUE, ASK_CONTA,
    ASK_CATEGORY, ASK_SUBCATEGORY, ASK_DATA,
    OCR_CONFIRMATION_STATE
) = range(80, 88)