# Frontend CitaLens

Interface React em JavaScript para o Autobibliograph Revision. React 19.1.0, Vite 6.3.5 e plugin React 4.5.2, com dependências transitivas registradas em `package-lock.json`. O build usa Node 22.16.0; o contêiner final usa nginx 1.28.0.

## Executar

O caminho principal é o Docker Compose na raiz do repositório. O Dockerfile usa a raiz como contexto:

```sh
docker build -f frontend/Dockerfile -t citalens-frontend .
```

Para desenvolvimento, com a API disponível em `localhost:8000`:

```sh
cd frontend
npm ci
npm run dev
```

`API_PROXY_TARGET` pode substituir o endereço da API no desenvolvimento. O nginx encaminha `/api/` para `backend-api:8000`; segredos de provedores não são enviados ao bundle. Em produção, configure TLS no proxy de entrada.

```sh
npm run build
npm run preview
```

## Comportamento

- Conta, autenticação, biblioteca paginada, upload e histórico por documento.
- Modo real é a opção inicial. Demonstração requer seleção explícita e é identificada em todas as telas da análise. Nenhum dado de exemplo está no código da aplicação.
- Polling com cancelamento e contagens retornadas pela API. Páginas, referências, ocorrências e pares têm denominadores separados; inconclusivos permanecem visíveis.
- Catálogo de referências, revisão humana de vínculos, cobertura física e rótulos impressos.
- Filtros de página, referência, gravidade e resultado; quatro dimensões independentes por parecer.
- Comparação entre afirmação e passagem da fonte; PDFs obtidos com autenticação e exibidos por URL `blob:`. A navegação é por página, sem destaque fictício de coordenadas.
- Anexação de PDF de fonte com confirmação explícita de identidade pelo usuário. Fontes e correções de vínculos são aplicadas em uma **nova análise**; resultados anteriores são preservados.
- Revisão humana de pareceres e download da versão mais recente do relatório que esteja pronta.

O token fica em `sessionStorage` e é removido ao sair ou receber HTTP 401. Não é incluído em URLs. O navegador é um cliente sem autoridade: validações de upload, autorização por objeto e confirmação de identidade continuam no backend. Os limites locais acompanham os padrões do backend: 30 MB por manuscrito e 20 MB por fonte complementar; o backend pode aplicar limites menores.

Documentos em exclusão permanecem visíveis e não podem ser abertos nem analisados. A lista atualiza automaticamente enquanto a exclusão avança. Se houver falha, o motivo é exibido com a opção de repetir a solicitação.

## Testes

```sh
npm ci
npx playwright install chromium
npm run test:e2e
```

Em uma máquina com Chrome instalado, é possível usar `CITALENS_BROWSER_PATH` para indicar o executável sem baixar outro navegador. Os testes em `e2e/review.spec.js` usam mocks HTTP declarados apenas nos testes. Cobrem autenticação, upload, modo demo, evidência, revisão humana, filtros, download, correção de vínculo, fonte complementar, layout móvel, arquivo inválido e sessão expirada.

O teste em `e2e/live.spec.js` usa o Compose real, sem mocks. Para executá-lo após iniciar os serviços:

```sh
# Linux/macOS
CITALENS_E2E_LIVE=1 CITALENS_BASE_URL=http://localhost:8080 npm run test:e2e -- e2e/live.spec.js
```

```powershell
# PowerShell
$env:CITALENS_E2E_LIVE = "1"
$env:CITALENS_BASE_URL = "http://localhost:8080"
npm run test:e2e -- e2e/live.spec.js
```

O teste cria uma conta de teste, obtém o manuscrito demonstrativo, inicia uma análise e valida o download como PDF. Ajuste a porta à configuração do Compose. Relatórios e screenshots são gravados em `playwright-report/` e `test-results/` e não são versionados.

Documentação oficial consultada: [React: efeitos](https://react.dev/reference/react/useEffect), [Vite: build de produção](https://vite.dev/guide/build) e [Vite: servir o build](https://vite.dev/guide/static-deploy.html).
