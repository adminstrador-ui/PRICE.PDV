import os
import time
import warnings
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Configurações Iniciais e Supressão de Avisos
warnings.filterwarnings("ignore", category=UserWarning)

# ==============================================================================
# CONFIGURAÇÕES DE ACESSO AO BANCO DE DADOS POSTGRESQL (HOSTINGER)
# ==============================================================================
DB_HOST = "179.199.138.119"
DB_PORT = "5432"
DB_NAME = "Minhabase"     
DB_USER = "ramonn"        
DB_PASS = "Synk2709@VPS"  

NOME_ARQUIVO_EXCEL = "Precos_Raspados.xlsx"

def executar_banco(q, d=None, ler=False):
    """Gerencia conexões e execuções no Banco de Dados PostgreSQL."""
    conexao_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"
    
    with psycopg2.connect(conexao_str) as conn:
        if ler:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(q, d if d else [])
                return [dict(line) for line in cur.fetchall()]

        else:
            with conn.cursor() as cur:
                cur.execute(q, d if d else [])
            conn.commit()

def salvar_historico_banco(lista_itens):
    """Insere de forma massiva os dados raspados respeitando a estrutura exata do banco."""
    conexao_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"
    
    # Query adaptada estritamente com as colunas reais da sua imagem
    query = """
        INSERT INTO historico_precos (
            dados_id, origem_busca, ean_retornado, produto_descricao,
            preco_unitario, preco_bruto, preco_final, valor_desconto, produto_ncm,
            loja_nome, loja_cnpj, loja_endereco, loja_cidade, loja_bairro, data_coleta
        ) VALUES %s
    """
    
    # Tratamento e alinhamento dos dados extraídos pelo dicionário com as colunas reais
    valores = [
        (
            int(item["ID Pai (DADOS)"]),
            item["Origem Busca"][:20] if item["Origem Busca"] else None,
            str(item.get("Código de Barras Retornado", item["Código de Barras Pesquisado"]))[:20],
            item["Produto"][:255] if item["Produto"] else "Não informado",
            float(item.get("Preço Unitário", 0) or 0),
            float(item.get("Preço Bruto", 0) or 0),
            float(item.get("Preço Final", 0) or 0),
            float(item.get("Desconto", 0) or 0),
            str(item.get("NCM", "Não informado"))[:20],
            item["Loja"][:255] if item["Loja"] else "Não informado",
            str(item.get("CNPJ", "Não informado"))[:20],
            item.get("Endereço", "Não informado"),
            item["Cidade"][:100] if item["Cidade"] else "Não informado",
            item.get("Bairro", "Não informado")[:100],
            item["Data"]
        )
        for item in lista_itens
    ]
    
    with psycopg2.connect(conexao_str) as conn:
        with conn.cursor() as cur:
            # Envio em lote super veloz para o PostgreSQL da Hostinger
            execute_values(cur, query, valores)
        conn.commit()

def clicar_elemento(xpath, timeout_sleep=1.5):
    """Executa pausas estratégicas dinâmicas e clica de forma segura via XPath."""
    time.sleep(timeout_sleep)
    wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()

def processar_dados_interceptados(lista_api, r_rodada, ean_referencia, id_dados, cidade, codigo_barras_pai):
    """Processa o pacote de dados interceptado e adiciona na memória do script Python."""
    if not lista_api:
        return r_rodada
        
    for api_item in lista_api:
        ean_atual = api_item.get('ean', ean_referencia)
        api = api_item.get('json')
        
        if not api: 
            continue
        if isinstance(api, dict) and 'data' in api: 
            api = api['data']
            
        itens = api if isinstance(api, list) else next(
            (api[k] for k in ['resultado','produtos','conteudo','itens','historico','rows'] if k in api and api[k]), 
            [api] if any(k in api for k in ['descricao','nome','produto']) else []
        )
        if not isinstance(itens, list): 
            continue
        
        for it in itens:
            try:
                p = it.get('produto', it) if isinstance(it.get('produto'), dict) else it
                e = it.get('estabelecimento', it.get('emitente', {})) or {}
                v_final = it.get('precoFinal', p.get('precoFinal', it.get('precoVenda', it.get('preco', it.get('valor', it.get('precoUnitario', 0.0))))))

                item_dict = {
                    "ID Pai (DADOS)": id_dados, 
                    "Origem Busca": "PAI" if str(ean_atual) == str(codigo_barras_pai) else "CONCORRENTE", 
                    "Código de Barras Pesquisado": ean_atual, 
                    "Código de Barras Retornado": p.get('codProduto' if p.get('codProduto') else 'ean', ean_atual),
                    "Produto": p.get('descricao', p.get('nome', p.get('nomeProduto', 'Não informado'))), 
                    "Preço Unitário": p.get('precoUnitario', v_final),
                    "Preço Bruto": p.get('precoBruto', p.get('valorBruto', v_final)),
                    "Preço Final": v_final,
                    "Desconto": p.get('desconto', it.get('valorDesconto', 0.0)),
                    "NCM": p.get('ncm', 'Não informado'), 
                    "Loja": e.get('nomeEstabelecimento', e.get('razaoSocial', e.get('fantasia', 'Não informado'))),
                    "CNPJ": e.get('cnpj', 'Não informado'), 
                    "Endereço": e.get('endLogradouro', e.get('logradouro', 'Não informado')), 
                    "Cidade": e.get('municipio', cidade), 
                    "Bairro": e.get('bairro', 'Não informado'), 
                    "Data": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                r_rodada.append(item_dict)
            except Exception as e_item:
                try:
                    item_dict_fallback = {
                        "ID Pai (DADOS)": id_dados,
                        "Origem Busca": "PAI" if str(ean_atual) == str(codigo_barras_pai) else "CONCORRENTE",
                        "Código de Barras Pesquisado": ean_atual,
                        "Produto": "Erro na leitura estrutural do item",
                        "Preço Final": 0.0,
                        "Loja": "Incompleto",
                        "Data": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    r_rodada.append(item_dict_fallback)
                except:
                    pass
    return r_rodada

def forcar_rolagem_pagina():
    """Executa a rolagem forçada da página para carregar todos os itens assíncronos."""
    print("[ROBÔ] Rolando a página para forçar o carregamento de todos os resultados...")
    ultima_altura = nav.execute_script("return document.body.scrollHeight")
    for _ in range(10): 
        nav.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2.5) 
        try:
            elemento_update = nav.find_element(By.XPATH, "//*[@id='updateResults']")
            if elemento_update.is_displayed():
                nav.execute_script("arguments[0].click();", elemento_update)
                print("[ROBÔ] Elemento complementar de atualização carregado.")
                time.sleep(3)
        except:
            pass
        nova_altura = nav.execute_script("return document.body.scrollHeight")
        if nova_altura == ultima_altura:
            break 
        ultima_altura = nova_altura

# 1. Busca registros pendentes no banco
pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status) = 'PENDENTE'", ler=True)
if not pesquisas:
    print("[AVISO] Nenhum registro PENDENTE encontrado no banco. Finalizando robô.")
    exit()

# 2. Configurações de Ocultação de Automação (Anti-Bot)
o = Options()
o.add_experimental_option("detach", True)
o.add_experimental_option("excludeSwitches", ["enable-automation"])
o.add_argument("--disable-blink-features=AutomationControlled")
o.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
nav = webdriver.Chrome(options=o)

# 3. Injeção de Script CDP para Interceptação de API (Fetch & XHR)
nav.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
(function() {
    if (!window.dados_precos) window.dados_precos = [];
    const interceptar = (url, textoResponse) => { 
        if (['produto','pesquisa','v1','v2','historico','preco','comum'].some(k => url.toLowerCase().includes(k))) { 
            try { 
                let obj = JSON.parse(textoResponse); 
                if(obj) window.dados_precos.push({url: url, json: obj, ean: window.current_ean_target}); 
            } catch(e){} 
        } 
    };
    
    // Interceptando requisições tradicionais (XHR)
    let X = XMLHttpRequest.prototype, oOpen = X.open, oSend = X.send;
    X.open = function(m, u) { this._url = u; return oOpen.apply(this, arguments); };
    X.send = function() { 
        this.addEventListener('load', function() { interceptar(this._url, this.responseText); }); 
        return oSend.apply(this, arguments); 
    };
    
    // Interceptando a API moderna Fetch
    let originalFetch = window.fetch; 
    window.fetch = async function(...args) { 
        let response = await originalFetch(...args), clone = response.clone(); 
        try { 
            clone.json().then(data => { 
                if(data) window.dados_precos.push({url: args, json: data, ean: window.current_ean_target}); 
            }); 
        } catch(e){} 
        return response; 
    };
})();
"""})

# Inicialização do ambiente do portal
nav.get("https://precodahora.ba.gov.br")
nav.maximize_window()
wait = WebDriverWait(nav, 15)

# Abre o portal uma única vez antes do laço para economizar tempo de rede
nav.get("https://precodahora.ba.gov.br")
nav.maximize_window()
wait = WebDriverWait(nav, 15)

# --- CONTADOR GLOBAL DE HISTÓRICO ---
contador_total_coletas = 0

# ==============================================================================
# CONTROLE DO LOOP CONTÍNUO E SEGURANÇA (GERENCIADOR DA VM)
# ==============================================================================
try:
    while True:
        # 1. Busca os registros pendentes no banco a cada início de ciclo
        pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status) = 'PENDENTE'", ler=True)
        
        if not pesquisas:
            print("[AVISO] Nenhum registro PENDENTE encontrado no banco. Aguardando 10 minutos para checar novamente...")
            time.sleep(600)  # Aguarda 10 minutos (600 segundos) para não estressar o banco
            continue         # Retorna ao início do 'while' para fazer uma nova consulta

        print(f"[ROBÔ] Encontrados registros pendentes. Iniciando lote de segurança (Máximo 8 raspagens)...")
        
        # Limitador de segurança interno para o ciclo atual
        contador_ciclo_atual = 0

        # ==============================================================================
        # LAÇO PRINCIPAL DE PROCESSAMENTO (LIMITADO A 8 ITENS POR CICLO)
        # ==============================================================================
        for item in pesquisas:
            # Se atingiu o limite rígido de 8 raspagens neste intervalo, interrompe o 'for'
            if contador_ciclo_atual >= 8:
                break

            ID_DADOS = item["id_dados"]
            CIDADE = item["cidade"]
            CODIGO_BARRAS = item["codigo_barras"]

            print(f"\n=========================================")
            print(f"[ROBÔ] [{contador_ciclo_atual + 1}/8] Iniciando: {CIDADE} | EAN PAI: {CODIGO_BARRAS} | ID: {ID_DADOS}")
            print(f"=========================================")
            
            r_rodada = []  # Memória Python do lote atual (Limpa a cada novo ID)

            try:
                print("[ROBÔ] Preparando ambiente da página inicial...")
                nav.get("https://precodahora.ba.gov.br")
                time.sleep(4)

                # Configuração de Localidade (Filtro por Município)
                clicar_elemento("/html/body/header/div/div/nav/div/a/i") 
                clicar_elemento("//*[@id='sidebar-filtros-home']/div/div/div/button") 
                clicar_elemento("//*[@id='add-center']", timeout_sleep=2.5)
                
                input_cidade = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='modal-regions']//input[@type='text']")))
                input_cidade.click()
                input_cidade.send_keys(CIDADE)
                time.sleep(3)
                
                try: 
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']/ul/li[2]"))).click()
                except: 
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']/ul/li"))).click()
                    
                clicar_elemento("//*[@id='aplicar']")
                time.sleep(4)

                # --- FASE 1: BUSCA E CAPTURA DO PRODUTO PAI ---
                print(f"[PAI] Iniciando busca do EAN Pai: {CODIGO_BARRAS}")
                nav.execute_script(f"window.current_ean_target = '{CODIGO_BARRAS}';")
                nav.execute_script("window.dados_precos = [];")
                
                campo_busca = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                campo_busca.click()
                campo_busca.send_keys(Keys.CONTROL + "a")
                campo_busca.send_keys(Keys.DELETE)
                campo_busca.send_keys(str(CODIGO_BARRAS) + Keys.ENTER)
                time.sleep(4)
                
                forcar_rolagem_pagina()
                
                lista_api_pai = nav.execute_script("return window.dados_precos;")
                r_rodada = processar_dados_interceptados(lista_api_pai, r_rodada, CODIGO_BARRAS, ID_DADOS, CIDADE, CODIGO_BARRAS)
                print(f"[PAI] Capturados {len(r_rodada)} registros do produto Pai.")

                # --- FASE 2: BUSCA E CAPTURA DOS PRODUTOS CONCORRENTES FILHOS ---
                concorrentes = executar_banco('SELECT ean_concorrente FROM dados_concorrentes WHERE dados_id = %s', d=[ID_DADOS], ler=True)
                
                for conc in concorrentes:
                    ean_filho = conc.get("ean_concorrente") or conc.get("EAN_CONCORRENTE")
                    if ean_filho:
                        try:
                            print(f"[CONCORRENTE] Iniciando busca isolada do EAN Filho: {ean_filho}")
                            nav.execute_script(f"window.current_ean_target = '{ean_filho}';")
                            nav.execute_script("window.dados_precos = [];")
                            
                            try:
                                campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            except:
                                nav.get("https://precodahora.ba.gov.br")
                                time.sleep(4)
                                campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            
                            campo_filho.click()
                            campo_filho.send_keys(Keys.CONTROL + "a")
                            campo_filho.send_keys(Keys.DELETE)
                            campo_filho.send_keys(str(ean_filho) + Keys.ENTER)
                            time.sleep(4)
                            
                            forcar_rolagem_pagina()
                            
                            lista_api_filho = nav.execute_script("return window.dados_precos;")
                            registros_antes = len(r_rodada)
                            r_rodada = processar_dados_interceptados(lista_api_filho, r_rodada, ean_filho, ID_DADOS, CIDADE, CODIGO_BARRAS)
                            print(f"[CONCORRENTE] Capturados {len(r_rodada) - registros_antes} registros do filho {ean_filho}.")
                        except Exception as e_filho:
                            print(f"[AVISO] Falha ao processar concorrente {ean_filho}, pulando... Erro: {e_filho}")

                # --- FASE 3: PERSISTÊNCIA TOTAL DOS DADOS COLETADOS NO BANCO E EXCEL ---
                if r_rodada:
                    print(f"[BANCO] Gravando {len(r_rodada)} registros na tabela 'historico_precos'...")
                    salvar_historico_banco(r_rodada)
                    print("[BANCO] Dados salvos com sucesso no histórico.")
                    
                    df_novos_dados = pd.DataFrame(r_rodada)
                    if os.path.exists(NOME_ARQUIVO_EXCEL):
                        try:
                            df_antigo = pd.read_excel(NOME_ARQUIVO_EXCEL)
                            df_consolidado = pd.concat([df_antigo, df_novos_dados], ignore_index=True)
                        except Exception as ex_excel:
                            print(f"[AVISO] Falha ao ler Excel existente, criando novo arquivo: {ex_excel}")
                            df_consolidado = df_novos_dados
                    else:
                        df_consolidado = df_novos_dados
                        
                    df_consolidado.to_excel(NOME_ARQUIVO_EXCEL, index=False)
                    print(f"[EXCEL] Backup gerado com sucesso.")
                    
                    executar_banco("UPDATE DADOS SET status = 'PROCESSADO' WHERE ID = %s", d=[ID_DADOS])
                    print(f"[SUCESSO] ID {ID_DADOS} concluído com êxito total.")
                else:
                    print("[AVISO] Nenhuma requisição de API válida capturada para este lote (Pai/Filhos).")
                    executar_banco("UPDATE DADOS SET status = 'AVISO_SEM_DADOS' WHERE ID = %s", d=[ID_DADOS])

            except Exception as e:
                print(f"[ERRO] Ocorreu uma falha crítica no processamento do ID {ID_DADOS}: {e}")
                try:
                    executar_banco("UPDATE DADOS SET status = 'ERRO' WHERE ID = %s", d=[ID_DADOS])
                except Exception as database_error:
                    print(f"[ERRO BANCO] Impossível alterar status para ERRO: {database_error}")

            finally:
                nav.execute_script("window.dados_precos = [];")
                print("=" * 50)

            # Incrementa o controle do ciclo e o histórico acumulado
            contador_ciclo_atual += 1
            contador_total_coletas += 1

        # --- PAUSA OBRIGATÓRIA DE 10 MINUTOS APÓS O BLOCO DE 8 ITENS ---
        print(f"\n🛑 [SEGURANÇA] Ciclo concluído com {contador_ciclo_atual} raspagens realizadas.")
        print(f"[CONTAGEM] Total de lotes processados nesta sessão da VM: {contador_total_coletas}")
        print("⏰ Pausando o robô por exatamente 10 minutos para evitar bloqueios por IP da SEFAZ...")
        time.sleep(600)
        print("▶️ [ROBÔ] Pausa de 10 minutos concluída! Verificando novos registros pendentes...\n")

except KeyboardInterrupt:
    print("\n[AVISO] Execução interrompida manualmente pelo usuário.")

finally:
    # Garante o encerramento limpo do navegador se o processo em segundo plano for parado
    nav.quit()
    print("[FIM] Instância do navegador fechada com sucesso.")

