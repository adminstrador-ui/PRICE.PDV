import os
import time
import zipfile
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

from twocaptcha import TwoCaptcha

# Supressão de Avisos de Biblioteca
warnings.filterwarnings("ignore", category=UserWarning)

# ==============================================================================
# SEÇÃO 1: DIRETRIZES DE CREDENCIAIS (PROXIES, BANCO E CAPTCHA)
# ==============================================================================
API_KEY_CAPTCHA = "c599041e71136ea2aaa7165bff216429"

# Dados ISP obtidos diretamente do seu painel Bright Data
PROXY_HOST = "brd.superproxy.io"
PROXY_PORT = "44445"
PROXY_USER = "brd-customer-hl_35f154bd-zone-roboprice"
PROXY_PASS = "vrae85951ca9"

# Dados de conexão estruturais com o PostgreSQL (Hostinger)
DB_HOST = "179.199.138.119"
DB_PORT = "5432"
DB_NAME = "Minhabase"     
DB_USER = "ramonn"        
DB_PASS = "Synk2709@VPS"  

NOME_ARQUIVO_EXCEL = "Precos_Raspados.xlsx"

# ==============================================================================
# SEÇÃO 2: CONTROLADORES DE BANCO DE DADOS E BACKUP EXCEL
# ==============================================================================
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
    """Insere de forma massiva os dados raspados no PostgreSQL e atualiza o Excel."""
    conexao_str = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASS}"
    
    query = """
        INSERT INTO historico_precos (
            dados_id, origem_busca, ean_retornado, produto_descricao,
            preco_unitario, preco_bruto, preco_final, valor_desconto, produto_ncm,
            loja_nome, loja_cnpj, loja_endereco, loja_cidade, loja_bairro, data_coleta
        ) VALUES %s
    """
    
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
            execute_values(cur, query, valores)
        conn.commit()

    # Fluxo paralelo para atualização do backup consolidado em Excel
    df_novos_dados = pd.DataFrame(lista_itens)
    if os.path.exists(NOME_ARQUIVO_EXCEL):
        try:
            df_antigo = pd.read_excel(NOME_ARQUIVO_EXCEL)
            df_consolidado = pd.concat([df_antigo, df_novos_dados], ignore_index=True)
        except Exception as ex_excel:
            print(f"[AVISO] Falha ao ler Excel existente, gerando novo arquivo: {ex_excel}")
            df_consolidado = df_novos_dados
    else:
        df_consolidado = df_novos_dados
        
    df_consolidado.to_excel(NOME_ARQUIVO_EXCEL, index=False)
    print(f"[EXCEL] Backup gerado com sucesso.")

# ==============================================================================
# SEÇÃO 3: FUNÇÕES DE SEGURANÇA, BYPASS ANTI-BOT E RECAPTCHA
# ==============================================================================
def configurar_proxy_rotativo(options, host, port, user, password):
    """Injeta em tempo de execução uma extensão capaz de autenticar o proxy ISP da Bright Data."""
    manifest_json = """
    {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Chrome Proxy",
        "permissions": [
            "proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"
        ],
        "background": { "scripts": ["background.js"] },
        "minimum_chrome_version":"22.0.0"
    }
    """

    background_js = f"""
    var config = {{
            mode: "fixed_servers",
            rules: {{
              singleProxy: {{
                scheme: "http",
                host: "{host}",
                port: parseInt({port})
              }},
              bypassList: []
            }}
          }};

    chrome.proxy.settings.set({{value: config, scope: "regular"}}, function({{}});

    chrome.webRequest.onAuthRequired.addListener(
                function(details) {{
                    return {{
                        authCredentials: {{
                            username: "{user}",
                            password: "{password}"
                        }}
                    }};
                }},
                {{urls: ["<all_urls>"]}},
                ["blocking"]
            );
    """
    
    plugin_file = 'proxy_auth_plugin.zip'
    with zipfile.ZipFile(plugin_file, 'w') as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    
    options.add_extension(plugin_file)
    return options


def verificar_e_resolver_captcha():
    """Identifica a presença de desafios ReCAPTCHA e aciona a API do 2Captcha."""
    time.sleep(3) 
    url_atual = nav.current_url
    captcha_presente = False
    
    if "/challenge/" in url_atual:
        captcha_presente = True
    else:
        try:
            elementos = nav.find_elements(By.CLASS_NAME, "g-recaptcha") + nav.find_elements(By.XPATH, "//iframe[contains(@src, 'recaptcha')]")
            if elementos and any(el.is_displayed() for el in elementos):
                captcha_presente = True
        except:
            pass

    if not captcha_presente:
        return

    print("[AVISO INTERVENÇÃO] Bloqueio por reCAPTCHA detectado! Acionando 2Captcha de forma expressa...")
    try:
        solver = TwoCaptcha(API_KEY_CAPTCHA)
        try:
            sitekey = nav.find_element(By.CLASS_NAME, "g-recaptcha").get_attribute("data-sitekey")
        except:
            sitekey = "6Lfj7mEaAAAAANgG-5n5f_x95Z_Z1_G_g3_g8g_g" 
            
        print(f"[CAPTCHA] Sitekey identificada: {sitekey}. Aguardando resposta da API...")
        result = solver.recaptcha(sitekey=sitekey, url=url_atual)
        token_resposta = result['code']
        print("[CAPTCHA] Sucesso! Código recebido. Injetando de forma direta...")
        
        nav.execute_script(f'document.getElementById("g-recaptcha-response").innerHTML = "{token_resposta}";')
        try:
            nav.execute_script(f'document.getElementsByName("g-recaptcha-response").value = "{token_resposta}";')
        except:
            pass
            
        botao_enviar = nav.find_element(By.XPATH, "//button[contains(text(), 'Enviar')] | //input[@type='submit'] | //button[@type='submit'] | //*[@class='btn btn-primary']")
        botao_enviar.click()
        
        print("[CAPTCHA] Resposta enviada. Aguardando liberação da página...")
        time.sleep(5) 
    except Exception as e_captcha:
        print(f"[ERRO CRÍTICO CAPTCHA] Falha na quebra automatizada rápida: {e_captcha}")
# ==============================================================================
# SEÇÃO 4: INTEGRAÇÃO DA ROLAGEM, INTERCEPTADOR CDP E CLIQUES
# ==============================================================================
def clicar_elemento(xpath, timeout_sleep=1.5):
    """Executa cliques seguros controlados por tempo."""
    time.sleep(timeout_sleep)
    wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()


def forcar_rolagem_pagina():
    """Rola dinamicamente a página para carregar as requisições assíncronas."""
    print("[ROBÔ] Rolando a página para forçar o carregamento de todos os resultados...")
    ultima_altura = nav.execute_script("return document.body.scrollHeight")
    for _ in range(10): 
        nav.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2.5) 
        try:
            elemento_update = nav.find_element(By.XPATH, "//*[@id='updateResults']")
            if elemento_update.is_displayed():
                nav.execute_script("arguments.click();", elemento_update)
                time.sleep(3)
        except:
            pass
        nova_altura = nav.execute_script("return document.body.scrollHeight")
        if nova_altura == ultima_altura: break 
        ultima_altura = nova_altura


def processar_dados_interceptados(lista_api, r_rodada, ean_referencia, id_dados, cidade, codigo_barras_pai):
    """Interpreta os pacotes de API capturados pelo script CDP injetado."""
    if not lista_api: return r_rodada
    for api_item in lista_api:
        ean_atual = api_item.get('ean', ean_referencia)
        api = api_item.get('json')
        if not api: continue
        if isinstance(api, dict) and 'data' in api: api = api['data']
        itens = api if isinstance(api, list) else next((api[k] for k in ['resultado','produtos','conteudo','itens','historico','rows'] if k in api and api[k]), [api] if any(k in api for k in ['descricao','nome','produto']) else [])
        if not isinstance(itens, list): continue
        for it in itens:
            try:
                p = it.get('produto', it) if isinstance(it.get('produto'), dict) else it
                e = it.get('estabelecimento', it.get('emitente', {})) or {}
                v_final = it.get('precoFinal', p.get('precoFinal', it.get('precoVenda', it.get('preco', it.get('valor', it.get('precoUnitario', 0.0))))))
                item_dict = {
                    "ID Pai (DADOS)": id_dados, "Origem Busca": "PAI" if str(ean_atual) == str(codigo_barras_pai) else "CONCORRENTE", "Código de Barras Pesquisado": ean_atual, 
                    "Código de Barras Retornado": p.get('codProduto' if p.get('codProduto') else 'ean', ean_atual), "Produto": p.get('descricao', p.get('nome', p.get('nomeProduto', 'Não informado'))), 
                    "Preço Unitário": p.get('precoUnitario', v_final), "Preço Bruto": p.get('precoBruto', p.get('valorBruto', v_final)), "Preço Final": v_final, "Desconto": p.get('desconto', it.get('valorDesconto', 0.0)),
                    "NCM": p.get('ncm', 'Não informado'), "Loja": e.get('nomeEstabelecimento', e.get('razaoSocial', e.get('fantasia', 'Não informado'))), "CNPJ": e.get('cnpj', 'Não informado'), 
                    "Endereço": e.get('endLogradouro', e.get('logradouro', 'Não informado')), "Cidade": e.get('municipio', cidade), "Bairro": e.get('bairro', 'Não informado'), "Data": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                r_rodada.append(item_dict)
            except Exception: 
                try:
                    item_dict_fallback = {
                        "ID Pai (DADOS)": id_dados, "Origem Busca": "PAI" if str(ean_atual) == str(codigo_barras_pai) else "CONCORRENTE", "Código de Barras Pesquisado": ean_atual, 
                        "Produto": "Erro na leitura estrutural do item", "Preço Final": 0.0, "Loja": "Incompleto", "Data": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    r_rodada.append(item_dict_fallback)
                except: 
                    pass
    return r_rodada

from selenium.webdriver.common.action_chains import ActionChains  # Garanta que esta linha esteja no topo do código

def fazer_pesquisa_ean(ean_codigo):
    """Executa a limpeza rigorosa do campo de texto e digita o EAN com segurança."""
    campo_busca = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
    campo_busca.click()
    
    # Clique triplo para forçar a seleção de qualquer texto residual no campo
    actions = ActionChains(nav)
    actions.double_click(campo_busca).click(campo_busca).perform()
    
    campo_busca.clear()
    campo_busca.send_keys(Keys.BACKSPACE * 20) # Margem extra de segurança apagando caracteres
    time.sleep(0.5)
    
    campo_busca.send_keys(str(ean_codigo))
    time.sleep(0.8) # Tempo para o front-end processar a string
    campo_busca.send_keys(Keys.ENTER)
    time.sleep(2)

# ==============================================================================
# SEÇÃO 5: PREPARAÇÃO DA INSTÂNCIA DO CHROME WEBDRIVER
# ==============================================================================
pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status) = 'PENDENTE' ORDER BY CIDADE", ler=True)

if not pesquisas:
    print("[AVISO] Nenhum registro PENDENTE encontrado no banco. Finalizando robô.")
    exit()

o = Options()
o.add_experimental_option("detach", True)
o.add_experimental_option("excludeSwitches", ["enable-automation"])
o.add_argument("--disable-blink-features=AutomationControlled")
o.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Acoplamento do Proxy Residencial via Extensão do Chrome
o = configurar_proxy_rotativo(o, PROXY_HOST, PROXY_PORT, PROXY_USER, PROXY_PASS)

nav = webdriver.Chrome(options=o)

# Inicialização da escuta de requisições de API via CDP (Fetch/XHR)
nav.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "(function() { if (!window.dados_precos) window.dados_precos = []; const interceptar = (url, textoResponse) => { if (['produto','pesquisa','v1','v2','historico','preco','comum'].some(k => url.toLowerCase().includes(k))) { try { let obj = JSON.parse(textoResponse); if(obj) window.dados_precos.push({url: url, json: obj, ean: window.current_ean_target}); } catch(e){} } }; let X = XMLHttpRequest.prototype, oOpen = X.open, oSend = X.send; X.open = function(m, u) { this._url = u; return oOpen.apply(this, arguments); }; X.send = function() { this.addEventListener('load', function() { interceptar(this._url, this.responseText); }); return oSend.apply(this, arguments); }; let originalFetch = window.fetch; window.fetch = async function(...args) { let response = await originalFetch(...args), clone = response.clone(); try { clone.json().then(data => { if(data) window.dados_precos.push({url: args, json: data, ean: window.current_ean_target}); }); } catch(e){} return response; }; })();"})

nav.get("https://precodahora.ba.gov.br")
nav.maximize_window()
wait = WebDriverWait(nav, 15)

contador_total_coletas = 0

# ==============================================================================
# SEÇÃO 6: CICLO OPERACIONAL CONTÍNUO (GERENCIADOR DE LOTES OTIMIZADO)
# ==============================================================================
try:
    # Variável de controle para rastrear a última localidade configurada no navegador
    ultima_cidade_configurada = None

    while True:
        pesquisas = executar_banco("SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status) = 'PENDENTE'", ler=True)
        if not pesquisas:
            print("[AVISO] Nenhum registro PENDENTE encontrado. Aguardando 10 minutos...")
            time.sleep(600)
            continue

        print(f"[ROBÔ] Encontrados registros pendentes. Iniciando lote de segurança (Máximo 8)...")
        contador_ciclo_atual = 0

        for item in pesquisas:
            if contador_ciclo_atual >= 8: break
            ID_DADOS, CIDADE, CODIGO_BARRAS = item["id_dados"], item["cidade"], item["codigo_barras"]
            print(f"\n=========================================\n[ROBÔ] [{contador_ciclo_atual + 1}/8] Iniciando: {CIDADE} | EAN PAI: {CODIGO_BARRAS}\n=========================================")
            r_rodada = []

            try:
                # 🚀 OTIMIZAÇÃO CRÍTICA DE CIDADE:
                # Só recarrega a página e redefine o município se ele for DIFERENTE do último configurado
                if CIDADE != ultima_cidade_configurada:
                    print(f"[LOCALIDADE] Cidade mudou para {CIDADE} (ou é o primeiro item). Configurando filtros...")
                    nav.get("https://precodahora.ba.gov.br")
                    time.sleep(4)
                    
                    # Fluxo de cliques baseados nos seletores XPath reais mapeados da SEFAZ BA
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
                    
                    # Atualiza a memória com a cidade que acabou de ser configurada com sucesso
                    ultima_cidade_configurada = CIDADE
                else:
                    print(f"[⚡ OTIMIZAÇÃO] Mantendo a mesma cidade ({CIDADE}). Pulando configuração de filtros e mudando apenas o EAN!")

                # --- PRODUTO PAI ---
                print(f"[PAI] Buscando: {CODIGO_BARRAS}")
                nav.execute_script(f"window.current_ean_target = '{CODIGO_BARRAS}'; window.dados_precos = [];")
                fazer_pesquisa_ean(CODIGO_BARRAS)

                
                verificar_e_resolver_captcha()
                forcar_rolagem_pagina()
                lista_api_pai = nav.execute_script("return window.dados_precos;")
                r_rodada = processar_dados_interceptados(lista_api_pai, r_rodada, CODIGO_BARRAS, ID_DADOS, CIDADE, CODIGO_BARRAS)

                # --- PRODUTOS FILHOS CONCORRENTES ---
                concorrentes = executar_banco('SELECT ean_concorrente FROM dados_concorrentes WHERE dados_id = %s', d=[ID_DADOS], ler=True)
                for conc in concorrentes:
                    ean_filho = conc.get("ean_concorrente") or conc.get("EAN_CONCORRENTE")
                    if ean_filho:
                        try:
                            print(f"[CONCORRENTE] Buscando: {ean_filho}")
                            nav.execute_script(f"window.current_ean_target = '{ean_filho}'; window.dados_precos = [];")
                            try: 
                                campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            except:
                                nav.get("https://precodahora.ba.gov.br")
                                time.sleep(4)
                                campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                            
                            campo_filho.click()
                            fazer_pesquisa_ean(ean_filho)
                            
                            verificar_e_resolver_captcha()
                            forcar_rolagem_pagina()
                            lista_api_filho = nav.execute_script("return window.dados_precos;")
                            r_rodada = processar_dados_interceptados(lista_api_filho, r_rodada, ean_filho, ID_DADOS, CIDADE, CODIGO_BARRAS)
                        except Exception as e_filho: 
                            print(f"[AVISO] Erro no concorrente {ean_filho}: {e_filho}")

                # --- PERSISTÊNCIA DOS DADOS COLETADOS ---
                if r_rodada:
                    print(f"[BANCO] Gravando {len(r_rodada)} novos registros no histórico...")
                    salvar_historico_banco(r_rodada)
                    executar_banco("UPDATE DADOS SET status = 'PROCESSADO' WHERE ID = %s", d=[ID_DADOS])
                else:
                    executar_banco("UPDATE DADOS SET status = 'AVISO_SEM_DADOS' WHERE ID = %s", d=[ID_DADOS])

            except Exception as e:
                print(f"[ERRO CRÍTICO] Falha ao processar ID {ID_DADOS}: {e}")
                # Força o reset da memória de cidade em caso de falha crítica na navegação
                # para obrigar o robô a reconfigurar o portal no próximo item por segurança.
                ultima_cidade_configurada = None
                try: 
                    executar_banco("UPDATE DADOS SET status = 'ERRO' WHERE ID = %s", d=[ID_DADOS])
                except: 
                    pass
            finally:
                nav.execute_script("window.dados_precos = [];")

            contador_ciclo_atual += 1
            contador_total_coletas += 1

        print(f"\n🛑 [SEGURANÇA] Ciclo de lote finalizado com {contador_ciclo_atual} raspagens.")
        print(f"⏰ Entrando em pausa obrigatória de 10 minutos para proteção de rede...")
        time.sleep(600)

except KeyboardInterrupt: 
    print("\n[AVISO] Processo interrompido manualmente pelo usuário.")
finally:
    nav.quit()
    print("[FIM] Instância do navegador fechada com sucesso.")
