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
            loja_nome, loja_cnpj, loja_endereco, loja_cidade, loja_bairro, data_coleta, latitude, longitude
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
            item.get("Data", time.strftime("%Y-%m-%d %H:%M:%S")),
            # Mude as duas últimas linhas do seu print para ficarem assim:
            float(item.get("latitude", 0) or 0),
            float(item.get("longitude", 0) or 0)
    


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
def fechar_popup_anuncio():
    """Detecta a presença de pop-ups de anúncio iniciais do portal e os fecha com segurança."""
    time.sleep(2)  # Tempo para o pop-up renderizar na tela
    try:
        # Tenta localizar o botão 'X' do modal por XPaths mapeados do portal
        botoes_fechar = nav.find_elements(By.XPATH, "//button[@class='close'] | //*[contains(@class, 'modal')]//button[text()='×'] | //button[contains(@class, 'close-modal')]")
        for btn in botoes_fechar:
            if btn.is_displayed():
                btn.click()
                print("[ROBÔ] Pop-up de anúncio inicial localizado e fechado com sucesso!")
                time.sleep(1)
                return
    except Exception as e_popup:
        print(f"[AVISO] Falha ao tentar fechar pop-up (ou ele não apareceu): {e_popup}")


def clicar_elemento(xpath, timeout_sleep=1.5):
    """Executa cliques seguros controlados por tempo."""
    time.sleep(timeout_sleep)
    wait.until(EC.element_to_be_clickable((By.XPATH, xpath))).click()


def forcar_rolagem_pagina():
    """Rola dinamicamente a página por coordenadas para forçar o carregamento via AJAX."""
    print("[ROBÔ] Forçando rolagem de página para carregar todos os registros...")
    
    # Executa pequenas rolagens graduais para dar tempo ao AJAX de renderizar os cards
    for i in range(1, 6):
        nav.execute_script(f"window.scrollTo(0, (document.body.scrollHeight / 5) * {i});")
        time.sleep(1.5)
    
    # Rola até o final definitivo
    nav.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    
    # Tenta clicar no botão de carregar mais resultados (caso o portal exiba)
    try:
        # Seletor genérico para capturar o botão de paginação ou atualização do portal
        elemento_update = nav.find_elements(By.XPATH, "//*[@id='updateResults'] | //button[contains(@class, 'btn-carregar')] | //*[contains(text(), 'Carregar mais')]")
        for btn in elemento_update:
            if btn.is_displayed():
                nav.execute_script("arguments[0].click();", btn)
                print("[ROBÔ] Botão 'Carregar mais resultados' acionado via JS.")
                time.sleep(3)
                # Nova rolagem pós-clique para garantir captação
                nav.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    except Exception as e_scroll:
        print(f"[AVISO SCROLL] Sem botões adicionais para clicar: {e_scroll}")



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
                    "Endereço": e.get('endLogradouro', e.get('logradouro', 'Não informado')), "Cidade": e.get('municipio', cidade), "Bairro": e.get('bairro', 'Não informado'), "Data": time.strftime("%Y-%m-%d %H:%M:%S"), "latitude": e.get('latitude', 0), "longitude": e.get('longitude', 0)                }
                r_rodada.append(item_dict)
            except Exception: 
                try:
                    item_dict_fallback = {
                        "ID Pai (DADOS)": id_dados, "Origem Busca": "PAI" if str(ean_atual) == str(codigo_barras_pai) else "CONCORRENTE", "Código de Barras Pesquisado": ean_atual, 
                        "Produto": "Erro na leitura estrutural do item", "Preço Final": 0.0, "Loja": "Incompleto", "Data": time.strftime("%Y-%m-%d %H:%M:%S"), "latitude": 0, "longitude": 0
                    }
                    r_rodada.append(item_dict_fallback)
                except: 
                    pass
    return r_rodada

from selenium.webdriver.common.action_chains import ActionChains  # Garanta que esta linha esteja no topo do código

def fazer_pesquisa_ean(ean_codigo):
    """Executa a limpeza rigorosa do campo de texto e digita o EAN com segurança contra instabilidade."""
    try:
        # Aguarda o elemento de forma explícita
        campo_busca = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
        campo_busca.click()
        
        # Clique triplo para forçar a seleção de qualquer texto residual no campo
        actions = ActionChains(nav)
        actions.double_click(campo_busca).click(campo_busca).perform()
        
        campo_busca.clear()
        campo_busca.send_keys(Keys.BACKSPACE * 20)
        time.sleep(0.5)
        
        campo_busca.send_keys(str(ean_codigo))
        time.sleep(0.8)
        campo_busca.send_keys(Keys.ENTER)
        time.sleep(2)
    except Exception as e_busca:
        print(f"   [AVISO BUSCA] Campo instável, tentando recarregar o seletor para o EAN {ean_codigo}...")
        # Fallback: Se o elemento quebrar na memória, tenta localizá-lo de forma direta e bater o ENTER por JavaScript
        nav.get("https://precodahora.ba.gov.br")
        time.sleep(4)
        fechar_popup_anuncio()  # Garante que o banner não travou a busca de recuperação
        campo_busca = wait.until(EC.presence_of_element_located((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
        campo_busca.click()
        campo_busca.clear()
        campo_busca.send_keys(str(ean_codigo))
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
# SEÇÃO 6: CICLO OPERACIONAL CONTÍNUO (GERENCIADOR DE LOTES POR CIDADE ATIVA)
# ==============================================================================
try:
    while True:
        # 1. Coleta a lista completa de tarefas pendentes ordenadas por localidade
        pesquisas = executar_banco(
            "SELECT ID AS id_dados, CIDADE AS cidade, CODIGO_BARRAS FROM DADOS WHERE UPPER(status) = 'PENDENTE' ORDER BY CIDADE, ID DESC", 
            ler=True
        )
        
        if not pesquisas:
            print("[AVISO] Nenhum registro PENDENTE encontrado. Aguardando 3 minutos...")
            time.sleep(600)
            continue

        # 2. Transforma os dados em um DataFrame temporário para agrupar facilmente por cidade
        df_lote = pd.DataFrame(pesquisas)
        cidades_unicas = df_lote['cidade'].unique()
        
        print(f"[⚡ INTELIGÊNCIA] Iniciando ciclo massivo. Encontradas {len(cidades_unicas)} cidades diferentes para processar.")

        # 3. ROBUSTEZ: Loop focado estritamente na Cidade ativa da rodada
        for cidade_ativa in cidades_unicas:
            print(f"\n📍 [LOCALIDADE CENTRAL] Iniciando processamento em massa para a cidade: {cidade_ativa}")
            
            # Filtra apenas as tarefas pertencentes a esta cidade específica no lote atual
            tarefas_da_cidade = df_lote[df_lote['cidade'] == cidade_ativa].to_dict('records')
            print(f"📋 Encontrados {len(tarefas_da_cidade)} EANs principais pendentes em {cidade_ativa}.")

            try:
                # 🚀 PASSO 1: Configura a geolocalização no portal UMA ÚNICA VEZ para toda a cidade
                print(f"[FILTRO SEFAZ] Acessando o portal e travando a geolocalização para {cidade_ativa}...")
                nav.get("https://precodahora.ba.gov.br")
                time.sleep(4)

                # 🔒 ADICIONE ESTA LINHA AQUI: Remove o anúncio antes de tentar clicar nos filtros
                fechar_popup_anuncio()
                
                # Fluxo de cliques baseados nos seletores XPath reais mapeados da SEFAZ BA
                clicar_elemento("/html/body/header/div/div/nav/div/a/i")
                clicar_elemento("//*[@id='sidebar-filtros-home']/div/div/div/button")
                clicar_elemento("//*[@id='add-center']", timeout_sleep=2.5)
                
                input_cidade = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='modal-regions']//input[@type='text']")))
                input_cidade.click()
                input_cidade.send_keys(cidade_ativa)

                # Aguarda dinamicamente as sugestões aparecerem no mapa e clica na primeira opção disponível
                elemento_sugestao = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']//li")))
                elemento_sugestao.click()
                time.sleep(2)

                
                try: 
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']/ul/li[2]"))).click()
                except: 
                    wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='sugerir-municipios']/ul/li"))).click()
                    
                clicar_elemento("//*[@id='aplicar']")
                time.sleep(4)
                
            except Exception as e_config_cidade:
                print(f"❌ [ERRO CRÍTICO] Falha ao configurar a localidade {cidade_ativa}. Detalhes: {str(e_config_cidade)}")
                continue # Passa para a próxima cidade se a geolocalização falhar de início

            # 🚀 PASSO 2: Varre todos os EANs da mesma cidade sem recarregar os filtros
            for index, item in enumerate(tarefas_da_cidade):
                ID_DADOS = item["id_dados"]
                CODIGO_BARRAS = item["codigo_barras"]
                
                print(f"\n🔹 [{index + 1}/{len(tarefas_da_cidade)}] Processando item da fila | EAN PAI: {CODIGO_BARRAS}")
                r_rodada = []

                try:
                    # --- PRODUTO PAI ---
                    print(f"   ↳ [PAI] Buscando: {CODIGO_BARRAS}")
                    nav.execute_script(f"window.current_ean_target = '{CODIGO_BARRAS}'; window.dados_precos = [];")
                    fazer_pesquisa_ean(CODIGO_BARRAS)
                    
                    verificar_e_resolver_captcha()
                    forcar_rolagem_pagina()
                    lista_api_pai = nav.execute_script("return window.dados_precos;")
                    r_rodada = processar_dados_interceptados(lista_api_pai, r_rodada, CODIGO_BARRAS, ID_DADOS, cidade_ativa, CODIGO_BARRAS)

                    # --- PRODUTOS FILHOS CONCORRENTES ---
                    concorrentes = executar_banco('SELECT ean_concorrente FROM dados_concorrentes WHERE dados_id = %s', d=[ID_DADOS], ler=True)
                    for conc in concorrentes:
                        ean_filho = conc.get("ean_concorrente") or conc.get("EAN_CONCORRENTE")
                        if ean_filho and ean_filho != "SEM_CONCORRENTES":
                            try:
                                print(f"   ↳ [CONCORRENTE] Buscando: {ean_filho}")
                                nav.execute_script(f"window.current_ean_target = '{ean_filho}'; window.dados_precos = [];")
                                
                                # Captura ou força o retorno ao campo de busca caso a página mude de estado
                                try: 
                                    campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                                except:
                                    nav.get("https://precodahora.ba.gov.br")
                                    time.sleep(4)

                                                                    # Captura ou força o retorno ao campo de busca caso a página mude de estado
                                try: 
                                    campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                                except:
                                    nav.get("https://precodahora.ba.gov.br")
                                    time.sleep(4)
                                    
                                    # 🔒 ADICIONE ESTA LINHA AQUI TAMBÉM: Remove o anúncio se a página resetou
                                    fechar_popup_anuncio()
                                    
                                    campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))


                                    campo_filho = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[@id='fake-sbar'] | //input[@id='sbar']")))
                                
                                campo_filho.click()
                                fazer_pesquisa_ean(ean_filho)
                                
                                verificar_e_resolver_captcha()
                                forcar_rolagem_pagina()
                                lista_api_filho = nav.execute_script("return window.dados_precos;")
                                r_rodada = processar_dados_interceptados(lista_api_filho, r_rodada, ean_filho, ID_DADOS, cidade_ativa, CODIGO_BARRAS)
                            except Exception as e_filho: 
                                print(f"   [AVISO] Erro no concorrente {ean_filho}: {e_filho}")

                    # --- PERSISTÊNCIA DOS DADOS COLETADOS ---
                    if r_rodada:
                        print(f"   💾 [BANCO] Gravando {len(r_rodada)} novos registros no histórico...")
                        salvar_historico_banco(r_rodada)
                        executar_banco("UPDATE DADOS SET status = 'PROCESSADO' WHERE ID = %s", d=[ID_DADOS])
                    else:
                        print("   ⚠️ Nenhuma precificação interceptada para este produto.")
                        executar_banco("UPDATE DADOS SET status = 'AVISO_SEM_DADOS' WHERE ID = %s", d=[ID_DADOS])

                except Exception as e_item:
                    # Modificado para extrair o erro limpo e forçar restauração da aba
                    erro_limpo = str(e_item).split("\n")[0]
                    print(f"❌ [ERRO NO REGISTRO] Falha ao raspar ID {ID_DADOS}: {erro_limpo}")
                    try: 
                        executar_banco("UPDATE DADOS SET status = 'ERRO' WHERE ID = %s", d=[ID_DADOS])
                    except: 
                        pass
                    # Força o robô a recarregar a home no próximo item por segurança
                    nav.get("https://precodahora.ba.gov.br")
                    time.sleep(3)

                finally:
                    nav.execute_script("window.dados_precos = [];")

            print(f"✅ Concluído o lote da cidade: {cidade_ativa}. Avançando para a próxima...")
            # Pequena pausa tática entre cidades para aliviar o consumo de proxy residencial
            time.sleep(5)

        print("\n🏁 [CICLO TOTAL FINALIZADO] Todos as cidades pendentes foram processadas.")
        print("⏰ Entrando em pausa de segurança de 3 minutos antes da próxima varredura geral...")
        time.sleep(180)

except KeyboardInterrupt: 
    print("\n[AVISO] Processo interrompido manualmente pelo usuário.")
finally:
    nav.quit()
    print("[FIM] Instância do navegador fechada com sucesso.")
