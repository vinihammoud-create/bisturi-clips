; Instalador do Bisturi Clips (Inno Setup 6.5+ e 7.x; P5 do plano de publicação). Ponto de partida: docs/instalador/modelo_teste.iss (prova E).
; Arquivo em UTF-8 COM BOM (tem acento). O Pascal daqui só orquestra: a lógica pesada fica em Python (python -m cs2clip.instalacao).
;
; Compilar (o montar.py faz isto; todo /D abaixo é obrigatório, menos AppVersion e AppIdGuid):
;   ISCC.exe /DPacote=<pasta do pacote> /DAppVersion=<versão> /DFFmpegUrl=<url> /DFFmpegSha256=<sha256> /DFFmpegTamanhoExtraido=<bytes> /O<saida> BisturiClips.iss
;   <pasta do pacote> = app\ (cs2clip\, ui\, config.exemplo.toml, componentes.json, instalador\componentes.json, musicas\catalogo.json,
;   requirements-runtime.lock, requirements-gpu.lock, cs2clip\assets\sfx\*.wav) e uv.exe, os dois já conferidos pelo montar.py.
;   Os três /DFFmpeg* saem do instalador\componentes.json (ffmpeg: url, sha256 e tamanho_extraido), passados pelo montar.py --compilar;
;   FFmpegTamanhoExtraido = tamanho TOTAL descompactado do .7z (o ExternalSize do extractarchive). Instalador de teste: /DAppIdGuid={{<outro GUID>}.
; Instalar em silêncio (CI e testes):
;   BisturiClips-Setup-<versão>.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=<pasta> /DADOS=<pasta de dados> /LOG=<log>
;     [/SEMATALHO=1] [/SEMMODELO=1] [/SEMNVIDIA=1]
; Desinstalar: {app}\unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART [/APAGARDADOS=1]
; Códigos de saída: 0 ok · 4 um passo final falhou (abra o instalador de novo: ele retoma) · 7 o programa está aberto, a pasta de dados
; não serve ou o download do FFmpeg falhou (nada foi instalado).

#define VersaoApp "1.1.0"
#define Kernel32 "kernel32.dll"
#ifndef AppVersion
  #define AppVersion VersaoApp
#endif
; AppId do instalador REAL (a primeira chave "{" é dobrada de propósito). O de teste passa outro por /DAppIdGuid=.
#ifndef AppIdGuid
  #define AppIdGuid "{{56FE2A62-131E-403D-9A40-45940B607632}"
#endif
#ifndef Pacote
  #error Passe /DPacote=<pasta do pacote montado pelo instalador\montar.py>
#endif
#ifndef FFmpegUrl
  #error Passe /DFFmpegUrl= (vem do instalador\componentes.json, pelo montar.py)
#endif
#ifndef FFmpegSha256
  #error Passe /DFFmpegSha256= (vem do instalador\componentes.json, pelo montar.py)
#endif
#ifndef FFmpegTamanhoExtraido
  #error Passe /DFFmpegTamanhoExtraido= (tamanho_extraido do instalador\componentes.json, pelo montar.py)
#endif
; Sem Hash o Setup baixa e extrai sem conferir nada e o compilador não avisa (ACHADOS_POC_DEF, surpresa 2): aqui ele recusa.
#if Pos("https://github.com/", FFmpegUrl) != 1
  #error O FFmpeg só vem da release fixa do GitHub (https://github.com/GyanD/codexffmpeg/releases/download/<versão>/...)
#endif
#if Len(FFmpegSha256) != 64
  #error FFmpegSha256 precisa ter os 64 caracteres do SHA-256
#endif

[Setup]
AppId={#AppIdGuid}
AppName=Bisturi Clips
AppVersion={#AppVersion}
AppVerName=Bisturi Clips {#AppVersion}
AppPublisher=Bisturi Clips
DefaultDirName={autopf}\Bisturi Clips
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
; O Setup liga a mitigacao RedirectionGuard do Windows por padrao e os programas que ele inicia a HERDAM: o `uv` cria uma juncao de pasta
; (cpython-3.12-... -> cpython-3.12.x-...) e o Windows 11 recusa atravessa-la (erro 448, "ponto de montagem nao confiavel") quando o usuario
; instala sem ser administrador. O Setup nunca roda elevado aqui (PrivilegesRequired=lowest), entao a protecao nao guarda nada que importe.
RedirectionGuard=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputBaseFilename=BisturiClips-Setup-{#AppVersion}
SetupIconFile={#Pacote}\app\ui\static\icone.ico
UninstallDisplayIcon={app}\app\ui\static\icone.ico
UninstallDisplayName=Bisturi Clips
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName=Bisturi Clips
VersionInfoDescription=Instalador do Bisturi Clips
; extractarchive exige ArchiveExtraction diferente de "basic" (o padrão do 6.x); "enhanced/nopassword" existe no 6.5, 6.7 e 7.x.
ArchiveExtraction=enhanced/nopassword
; O Restart Manager encerra à força um programa sem janela (o servidor do Bisturi) e não enxerga o Python do venv, que não é do [Files]
; (surpresa 3). Quem confere o programa aberto é o PrepareToInstall, que nunca o derruba.
CloseApplications=no
RestartApplications=no
SetupLogging=yes
; Além dos arquivos do pacote e do FFmpeg (ExternalSize): venv com cuBLAS ~1,6 GB, modelo de voz 1,6 GB, músicas 0,2 GB,
; HLAE e boiler 0,13 GB, Python 0,07 GB e FFmpeg instalado 0,22 GB = ~4 GB (medidas da P1/P3).
ExtraDiskSpaceRequired=4200000000

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "atalho"; Description: "Criar um atalho na Área de Trabalho"
Name: "musicas"; Description: "Baixar as músicas livres para as montagens (CC0, cerca de 170 MB)"
Name: "modelo"; Description: "Baixar agora o modelo de voz (1,6 GB com placa NVIDIA; 0,5 GB sem)"
Name: "demo"; Description: "Gravar pela demo: instalar o HLAE e o boiler (cerca de 130 MB no disco, cerca de 10 MB de download)"; Flags: unchecked

[InstallDelete]
; o código velho some antes da cópia; app\bin (FFmpeg, TwitchDownloader, HLAE) e os dados ficam
Type: filesandordirs; Name: "{app}\app\cs2clip"
Type: filesandordirs; Name: "{app}\app\ui"

[Files]
Source: "{#Pacote}\app\*"; DestDir: "{app}\app"; Excludes: "__pycache__,*.pyc,pasta_dados.txt"; Flags: ignoreversion recursesubdirs createallsubdirs
; FFmpeg: baixado com Hash e extraído em {tmp}; o [Code] copia só ffmpeg.exe e ffprobe.exe para app\bin (o resto some com o {tmp}).
; Já instalado com o mesmo hash: não baixa de novo.
Source: "{#FFmpegUrl}"; DestName: "ffmpeg.7z"; DestDir: "{tmp}\ffx"; ExternalSize: {#FFmpegTamanhoExtraido}; Hash: "{#FFmpegSha256}"; Flags: external download extractarchive ignoreversion recursesubdirs createallsubdirs; Check: PrecisaBaixarFFmpeg
; ÚLTIMA entrada: os passos finais rodam no AfterInstall dela, antes de os atalhos ([Icons]) serem criados
Source: "{#Pacote}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: PassosFinais

[Icons]
; só aparecem se os passos finais deram certo (nunca um atalho para uma instalação pela metade)
Name: "{autoprograms}\Bisturi Clips"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m ui"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\ui\static\icone.ico"; Check: AtalhoMenuIniciar
Name: "{autodesktop}\Bisturi Clips"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m ui"; WorkingDir: "{app}\app"; IconFilename: "{app}\app\ui\static\icone.ico"; Check: AtalhoAreaDeTrabalho

[UninstallDelete]
; só as pastas que o instalador cria (se a pessoa escolheu uma pasta com outras coisas para o programa, elas ficam)
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\cache-uv"
Type: files; Name: "{app}\instalacao.log"
Type: files; Name: "{app}\atalhos_criados.txt"
Type: dirifempty; Name: "{app}"

[Code]
// Exceção em AfterInstall NÃO derruba o Setup (sairia com código 0, instalado e com atalhos): os passos finais pegam a exceção,
// marcam PassosFalharam e o Setup sai com código 4 em DeinitializeSetup (ACHADOS_POC_DEF, surpresa 4).
// (o nome da DLL vem do #define Kernel32: escrito por extenso logo depois da arroba, o varredor do exportador público o lê como e-mail)
procedure ExitProcess(CodigoSaida: Cardinal); external 'ExitProcess@{#Kernel32} stdcall';
// As variáveis de ambiente valem para os programas que o Setup abre (uv e Python); nada vai para a linha de comando.
function SetEnvironmentVariable(Nome: String; Valor: String): Longint; external 'SetEnvironmentVariableW@{#Kernel32} stdcall';

const
  PORTA_PRIMEIRA = 8765;   // as portas que a interface usa (ui/__main__.py)
  PORTA_ULTIMA = 8775;
  ETAPAS = 4;
  // o pos (cs2clip/instalacao.py) grava este marcador na pasta de dados; sem ele o desinstalador não apaga nada dela
  MARCADOR_DADOS = '.bisturi-dados';
  PROXY_DIRETO = 1;        // HTTPREQUEST_PROXYSETTING_DIRECT do WinHttpRequest: o 127.0.0.1 nunca passa por proxy
  PRAZO_HEALTH_S = 5;      // segundos para o /health responder depois de conectar
  // as linhas do {app}\atalhos_criados.txt (o marcador dos atalhos que ESTE instalador criou numa instalação que deu certo)
  ATALHO_MENU = 'menu_iniciar';
  ATALHO_AREA = 'area_de_trabalho';

var
  PassosFalharam: Boolean;
  MensagemFalha: String;
  PaginaDados: TInputDirWizardPage;
  PaginaNvidia: TInputOptionWizardPage;
  PaginaAndamento: TOutputProgressWizardPage;
  TemNvidia: Boolean;
  NomePlaca, VersaoDriver: String;
  EtapaAtual, Avisos, UltimaLinha: String;
  ApagarTudo, ApagarModelo: Boolean;
  DadosDesinstalar: String;

// ---- parâmetros próprios: /NOME=valor, lidos com {param:NOME|padrao} ----
function Param(const Nome, Padrao: String): String;
begin
  Result := ExpandConstant('{param:' + Nome + '|' + Padrao + '}');
end;

// ---- log em {app}\instalacao.log (e no log do Inno) ----
function ArquivoDeLog: String;
begin
  Result := ExpandConstant('{app}') + '\instalacao.log';
end;

procedure Registrar(const S: String);
var
  Linhas: TArrayOfString;
begin
  Log(S);
  SetArrayLength(Linhas, 1);
  Linhas[0] := GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + '  ' + S;
  if DirExists(ExpandConstant('{app}')) then
    SaveStringsToUTF8File(ArquivoDeLog, Linhas, True);
end;

// ---- caminho como UM argumento de comando: entre aspas duplas; TODAS as barras do fim são dobradas (senão "C:\" viraria \" escapado
// e "C:\x\\" também: a regra do Windows é 2n barras antes da aspa = n barras) ----
function Aspas(const S: String): String;
var
  I: Integer;
begin
  if (S = '') or (Pos(#34, S) > 0) then
    RaiseException('caminho inválido para um comando: [' + S + ']');
  Result := S;
  I := Length(S);
  while (I > 0) and (Copy(S, I, 1) = '\') do
  begin
    Result := Result + '\';
    I := I - 1;
  end;
  Result := #34 + Result + #34;
end;

procedure DefinirVariavel(const Nome, Valor: String);
begin
  if SetEnvironmentVariable(Nome, Valor) = 0 then
    RaiseException('não consegui definir a variável ' + Nome);
end;

// ---- tarefas e atalhos ----
function QuerModelo: Boolean;
begin
  Result := WizardIsTaskSelected('modelo') and (Param('SEMMODELO', '0') <> '1');
end;

function UsarNvidia: Boolean;
begin
  Result := False;
  if Param('SEMNVIDIA', '0') = '1' then
    Exit;
  if PaginaNvidia <> nil then
    Result := PaginaNvidia.Values[0]
  else
    Result := TemNvidia;
end;

function IconesPodem: Boolean;
begin
  Result := (not PassosFalharam) and (Param('SEMATALHO', '0') <> '1');
end;

function AtalhoMenuIniciar: Boolean;
begin
  Result := IconesPodem;
end;

function AtalhoAreaDeTrabalho: Boolean;
begin
  Result := IconesPodem and WizardIsTaskSelected('atalho');
end;

function LinkMenuIniciar: String;
begin
  Result := ExpandConstant('{autoprograms}\Bisturi Clips.lnk');
end;

function LinkAreaDeTrabalho: String;
begin
  Result := ExpandConstant('{autodesktop}\Bisturi Clips.lnk');
end;

// Um atalho com este nome pode ser de OUTRO programa (P9, F1). O unins000.exe não prova nada (ele fica depois de uma 1ª instalação que
// falhou: revisão final da 1.1.0, item 4): nosso é só o atalho que este marcador lista, gravado no fim de uma instalação que deu certo.
function ArquivoAtalhosCriados: String;
begin
  Result := ExpandConstant('{app}\atalhos_criados.txt');
end;

function AtalhoNoMarcador(const Atalho: String): Boolean;
var
  Linhas: TArrayOfString;
  I: Integer;
begin
  Result := False;
  if LoadStringsFromFile(ArquivoAtalhosCriados, Linhas) then
    for I := 0 to GetArrayLength(Linhas) - 1 do
      if Trim(Linhas[I]) = Atalho then
        Result := True;
end;

// Fim de uma instalação que deu certo (ssPostInstall, depois do [Icons]): o marcador lista os atalhos que existem E são nossos (os que o
// [Icons] acabou de criar, pelo mesmo Check, ou os que o marcador já listava). Sem nenhum, o marcador sai.
procedure GravarAtalhosCriados;
var
  Linhas: TArrayOfString;
  N: Integer;
begin
  SetArrayLength(Linhas, 2);
  N := 0;
  if FileExists(LinkMenuIniciar) and (AtalhoMenuIniciar or AtalhoNoMarcador(ATALHO_MENU)) then
  begin
    Linhas[N] := ATALHO_MENU;
    N := N + 1;
  end;
  if FileExists(LinkAreaDeTrabalho) and (AtalhoAreaDeTrabalho or AtalhoNoMarcador(ATALHO_AREA)) then
  begin
    Linhas[N] := ATALHO_AREA;
    N := N + 1;
  end;
  SetArrayLength(Linhas, N);
  if N = 0 then
    DeleteFile(ArquivoAtalhosCriados)
  else if not SaveStringsToFile(ArquivoAtalhosCriados, Linhas, False) then
    Log('não consegui gravar o marcador dos atalhos: numa atualização que falhar, os atalhos ficam');
  Log('atalhos deste instalador no marcador: ' + IntToStr(N));
end;

// atualização que falhou: o atalho velho apontaria para o venv pela metade. Sai só o que o marcador lista; depois sai o marcador
// (um atalho de mesmo nome criado depois não é nosso).
procedure RemoverAtalhos;
begin
  if (Param('SEMATALHO', '0') = '1') or (not FileExists(ArquivoAtalhosCriados)) then
  begin
    Log('atalhos mantidos: nenhum foi criado por este instalador numa instalação que deu certo (ou /SEMATALHO=1)');
    Exit;
  end;
  if AtalhoNoMarcador(ATALHO_AREA) then
    DeleteFile(LinkAreaDeTrabalho);
  if AtalhoNoMarcador(ATALHO_MENU) then
    DeleteFile(LinkMenuIniciar);
  DeleteFile(ArquivoAtalhosCriados);
end;

// ---- pasta de dados ----
function PastaVideos: String;
var
  Valor: String;
begin
  Result := ExpandConstant('{%USERPROFILE}') + '\Videos';
  if RegQueryStringValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders', 'My Video', Valor) then
    if Trim(Valor) <> '' then
      Result := Trim(Valor);
end;

function DadosPadrao: String;
begin
  Result := RemoveBackslash(PastaVideos) + '\Bisturi Clips';
end;

// /DADOS= vence; senão o valor da página (que parte do valor da instalação anterior)
function PastaDados: String;
begin
  Result := Trim(Param('DADOS', ''));
  if (Result = '') and (PaginaDados <> nil) then
    Result := Trim(PaginaDados.Values[0]);
  if Result = '' then
    Result := DadosPadrao;
  Result := RemoveBackslash(Result);
end;

// O formato do caminho (vale para o instalador e para o desinstalador). Mais estrito que o Python (cs2clip/caminhos.py e instalacao.py),
// nunca mais fraco: letra de disco e ":\", não é a raiz, sem as pastas "." e ".." (nem nome terminado em ponto ou espaço, que o Windows
// corta: "D:\." e "D:\x\.." seriam a raiz), sem barra dupla e sem caractere que o Windows não aceita. '' = serve.
function ProblemaNoCaminho(const Dados: String): String;
var
  I: Integer;
  Caminho: String;
begin
  Result := '';
  Caminho := AddBackslash(Dados);
  if (Length(Dados) < 4) or (Copy(Dados, 2, 2) <> ':\') or (Pos(Lowercase(Copy(Dados, 1, 1)), 'abcdefghijklmnopqrstuvwxyz') = 0) then
    Result := 'precisa ser o caminho completo de uma pasta num disco deste PC (por exemplo D:\Bisturi Clips), sem ser a raiz do disco'
  else if (Pos('.\', Caminho) > 0) or (Pos(' \', Caminho) > 0) or (Pos('\\', Caminho) > 0) then
    Result := 'não pode ter as pastas . ou .., barra dupla, nem nome de pasta terminado em ponto ou espaço'
  else if Pos(':', Copy(Dados, 3, Length(Dados))) > 0 then
    Result := 'tem um caractere que o Windows não aceita em nome de pasta (:)'
  else
    for I := 1 to Length(Dados) do
      if (Result = '') and ((Ord(Dados[I]) < 32) or (Ord(Dados[I]) = 34) or (Pos(Copy(Dados, I, 1), '<>|?*/') > 0)) then
        Result := 'tem um caractere que o Windows não aceita em nome de pasta (< > | ? * / aspas ou de controle)';
end;

// as mesmas regras do `pos` (cs2clip/instalacao.py, _validar_dados): o disco existe, não é um arquivo, não fica dentro da pasta do
// programa nem a contém (o {app} é a pasta-mãe do app\ que o Python confere)
function DadosValidos(const Dados: String; var Motivo: String): Boolean;
var
  App, Caminho: String;
begin
  Motivo := ProblemaNoCaminho(Dados);
  App := Lowercase(AddBackslash(ExpandConstant('{app}')));
  Caminho := Lowercase(AddBackslash(Dados));
  if Motivo <> '' then
    Motivo := 'A pasta de dados ' + Motivo + ': ' + Dados
  else if not DirExists(Copy(Dados, 1, 3)) then
    Motivo := 'O disco ' + Copy(Dados, 1, 3) + ' da pasta de dados não existe neste PC (se é um disco externo, ligue-o): ' + Dados
  else if FileExists(Dados) then
    Motivo := 'A pasta de dados é um arquivo, não uma pasta: ' + Dados
  else if (Pos(App, Caminho) = 1) or (Pos(Caminho, App) = 1) then
    Motivo := 'A pasta de dados não pode ficar dentro da pasta do programa nem conter a pasta do programa (ela é trocada nas atualizações '
      + 'e apagada ao desinstalar): ' + Dados;
  Result := Motivo = '';
end;

// a pasta de dados que o programa já usa (o pasta_dados.txt de uma instalação anterior; o desinstalador também lê daqui)
function DadosRegistrados: String;
var
  Linhas: TArrayOfString;
begin
  Result := '';
  // LoadStringsFromFile respeita o BOM do UTF-8 (acento e apóstrofo conferidos na prova E)
  if LoadStringsFromFile(ExpandConstant('{app}\app\pasta_dados.txt'), Linhas) and (GetArrayLength(Linhas) > 0) then
    Result := RemoveBackslash(Trim(Linhas[0]));
end;

// Trocar a pasta de dados de quem já usa o programa esconderia os vídeos, os votos e o config dele: como o pos (instalacao.py), recusa.
// Também em silêncio com /DADOS= diferente (nada de trocar calado). '' = sem conflito.
function ConflitoComDadosRegistrados(const Dados: String): String;
var
  Registrada: String;
begin
  Result := '';
  Registrada := DadosRegistrados;
  if (Registrada <> '') and (Lowercase(Registrada) <> Lowercase(Dados)) then
    Result := 'Este programa já usa outra pasta de dados (' + Registrada + '). Não troquei, para nada seu sumir de vista. Escolha '
      + Registrada + ' (em silêncio: /DADOS= com ela, ou sem /DADOS), ou desinstale e instale de novo para usar ' + Dados + '.';
end;

// só grava se ainda não existe (atualização: já aponta para a mesma pasta; ilegível: o pos confere e explica, sem mexer nele)
procedure GravarPastaDados(const Dados: String);
var
  Linhas: TArrayOfString;
  Arquivo, Motivo: String;
begin
  Arquivo := ExpandConstant('{app}\app\pasta_dados.txt');
  Motivo := ConflitoComDadosRegistrados(Dados);
  if Motivo <> '' then
    RaiseException(Motivo);
  if FileExists(Arquivo) then
    Exit;
  // UTF-8 com BOM (o programa lê com utf-8-sig; o desinstalador, com LoadStringsFromFile)
  SetArrayLength(Linhas, 1);
  Linhas[0] := Dados;
  if not SaveStringsToUTF8File(Arquivo, Linhas, False) then
    RaiseException('não consegui gravar o arquivo pasta_dados.txt');
end;

// ---- placa NVIDIA (nvidia-smi -L) ----
procedure SaidaNvidiaL(const S: String; const Error, FirstLine: Boolean);
var
  P: Integer;
begin
  Log('[nvidia-smi] ' + S);
  if Error or TemNvidia or (Pos('GPU ', S) <> 1) then
    Exit;
  TemNvidia := True;
  NomePlaca := S;
  P := Pos(': ', NomePlaca);
  if P > 0 then
    NomePlaca := Copy(NomePlaca, P + 2, Length(NomePlaca));
  P := Pos(' (UUID', NomePlaca);
  if P > 0 then
    NomePlaca := Copy(NomePlaca, 1, P - 1);
end;

procedure SaidaNvidiaDriver(const S: String; const Error, FirstLine: Boolean);
begin
  if (not Error) and (VersaoDriver = '') and (Trim(S) <> '') then
    VersaoDriver := Trim(S);
end;

// O Setup é 32 bits: o FileExists('{sys}\...') é redirecionado ao SysWOW64, que não tem o nvidia-smi (instalava sempre em CPU: P9, F2).
// {sysnative} é o System32 de verdade; {sys} fica de segundo palpite (instalador 64 bits) e depois a pasta antiga do NVSMI.
function ProgramaNvidiaSmi: String;
begin
  Result := ExpandConstant('{sysnative}\nvidia-smi.exe');
  if not FileExists(Result) then
    Result := ExpandConstant('{sys}\nvidia-smi.exe');
  if not FileExists(Result) then
    Result := ExpandConstant('{commonpf64}\NVIDIA Corporation\NVSMI\nvidia-smi.exe');
  if not FileExists(Result) then
    Result := '';
end;

procedure DetectarNvidia;
var
  Smi: String;
  Codigo: Integer;
begin
  TemNvidia := False;
  NomePlaca := '';
  VersaoDriver := '';
  if Param('SEMNVIDIA', '0') = '1' then
    Exit;
  Smi := ProgramaNvidiaSmi;
  if Smi = '' then
  begin
    Log('nvidia-smi.exe não encontrado: modo CPU');
    Exit;
  end;
  Log('nvidia-smi: ' + Smi);
  try
    if not ExecAndLogOutput(Smi, '-L', '', SW_HIDE, ewWaitUntilTerminated, Codigo, @SaidaNvidiaL) or (Codigo <> 0) then
      TemNvidia := False;
    if TemNvidia then
      ExecAndLogOutput(Smi, '--query-gpu=driver_version --format=csv,noheader', '', SW_HIDE, ewWaitUntilTerminated, Codigo, @SaidaNvidiaDriver);
  except
    TemNvidia := False;
    Log('nvidia-smi falhou: ' + GetExceptionMessage);
  end;
end;

procedure CriarPaginaNvidia;
var
  Texto: String;
begin
  if TemNvidia then
  begin
    Texto := 'Placa encontrada: ' + NomePlaca;
    if VersaoDriver <> '' then
      Texto := Texto + ' (driver ' + VersaoDriver + ')';
    Texto := Texto + '.' + #13#10#13#10 + 'Marcado: o Bisturi Clips usa a placa para entender a voz bem mais rápido (baixa cerca de 0,8 GB a mais) '
      + 'e o modelo de voz completo (1,6 GB). Desmarcado: usa só o processador, com o modelo small (0,5 GB).';
  end
  else
    Texto := 'Não encontrei uma placa NVIDIA neste PC (o programa nvidia-smi não respondeu).' + #13#10#13#10
      + 'O Bisturi Clips vai funcionar no processador (modo CPU): a parte de entender a voz fica mais lenta e usa o modelo small (0,5 GB).';
  PaginaNvidia := CreateInputOptionPage(wpSelectTasks, 'Placa de vídeo', 'Usar a placa NVIDIA para entender a voz?', Texto, False, False);
  PaginaNvidia.Add('Usar a placa de vídeo NVIDIA');
  PaginaNvidia.Values[0] := TemNvidia and (GetPreviousData('Nvidia', '1') <> '0');
  if not TemNvidia then
    PaginaNvidia.CheckListBox.ItemEnabled[0] := False;
end;

// ---- o programa está aberto? (/health em 127.0.0.1; o vigia da janela não o encerra com trabalho rodando: R-001) ----
function RespondeComoBisturi(Porta: Integer): Boolean;
var
  Pedido: Variant;
  Texto: String;
  Respondeu: Boolean;
begin
  Result := False;
  try
    Pedido := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Pedido.SetProxy(PROXY_DIRETO);
    // porta fechada no Windows leva ~1 s para recusar (ui/__main__.py, portas_ocupadas): conexão com prazo curto, e o erro dela é
    // "nada escutando". O pedido é assíncrono: quem aceitou a conexão e não respondeu em PRAZO_HEALTH_S (o servidor ocupado) conta
    // como o programa ABERTO, nunca como fechado.
    Pedido.SetTimeouts(500, 500, 1000, 30000);
    Pedido.Open('GET', 'http://127.0.0.1:' + IntToStr(Porta) + '/health', True);
    Pedido.Send('');
    Respondeu := Pedido.WaitForResponse(PRAZO_HEALTH_S);
    if not Respondeu then
    begin
      Log('a porta ' + IntToStr(Porta) + ' aceitou a conexão e não respondeu em ' + IntToStr(PRAZO_HEALTH_S) + ' s: conta como aberto');
      Result := True;
    end
    else if Pedido.Status = 200 then
    begin
      Texto := Pedido.ResponseText;   // Variant -> String antes do Lowercase (o compilador recusa o Variant direto)
      Texto := Lowercase(Texto);
      Result := (Pos('ok', Texto) > 0) and (Pos('true', Texto) > 0);
    end;
  except
    Result := False;   // erro de conexão: nada escutando nessa porta (o normal)
  end;
end;

function PortaDoProgramaAberto: Integer;
var
  Porta: Integer;
begin
  Result := 0;
  for Porta := PORTA_PRIMEIRA to PORTA_ULTIMA do
    if (Result = 0) and RespondeComoBisturi(Porta) then
      Result := Porta;
end;

// Nunca derruba o programa: pede para a pessoa encerrar pela tela e tenta de novo. Em silêncio (/SUPPRESSMSGBOXES) a resposta é
// Cancelar e o Setup para com a mensagem no log (código 7 na instalação).
function EsperarProgramaFechado(const Acao: String): String;
var
  Porta: Integer;
begin
  Result := '';
  Porta := PortaDoProgramaAberto;
  while Porta <> 0 do
  begin
    Result := 'O Bisturi Clips está aberto (porta ' + IntToStr(Porta) + '). Para ' + Acao + ', feche-o primeiro: se houver uma análise, '
      + 'um trabalho, uma gravação ou um download rodando, espere terminar e depois clique em “Encerrar o programa” na tela do Bisturi. '
      + 'O instalador não fecha o programa sozinho para não interromper o seu trabalho. (Se o Bisturi não estiver aberto, outro '
      + 'programa nessa porta não respondeu: feche-o.)';
    Log(Result);
    if SuppressibleMsgBox(Result + #13#10#13#10 + 'Clique em Repetir depois de encerrar o programa.', mbError, MB_RETRYCANCEL, IDCANCEL) <> IDRETRY then
      Exit;
    Porta := PortaDoProgramaAberto;
  end;
  Result := '';
end;

// ---- assistente ----
function InitializeSetup: Boolean;
begin
  Log('Bisturi Clips {#AppVersion}: DADOS=[' + Param('DADOS', '') + '] SEMATALHO=' + Param('SEMATALHO', '0') + ' SEMMODELO='
    + Param('SEMMODELO', '0') + ' SEMNVIDIA=' + Param('SEMNVIDIA', '0'));
  Result := True;
end;

procedure InitializeWizard;
begin
  PaginaDados := CreateInputDirPage(wpSelectDir, 'Pasta de dados',
    'Onde guardar os seus vídeos, kits, configuração e o modelo de voz?',
    'Esta pasta fica separada do programa: atualizar ou desinstalar o Bisturi Clips não apaga nada dela (a menos que você peça ao desinstalar).',
    False, '');
  PaginaDados.Add('');
  PaginaDados.Values[0] := GetPreviousData('PastaDados', DadosPadrao);
  if Param('DADOS', '') <> '' then
    PaginaDados.Values[0] := Param('DADOS', '');
  DetectarNvidia;
  CriarPaginaNvidia;
  PaginaAndamento := CreateOutputProgressPage('Instalando', 'Preparando o Bisturi Clips. Isto pode levar alguns minutos.');
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'PastaDados', PastaDados);
  if UsarNvidia then
    SetPreviousData(PreviousDataKey, 'Nvidia', '1')
  else
    SetPreviousData(PreviousDataKey, 'Nvidia', '0');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  // atualização: a página de dados é pulada e vale a pasta anterior
  if (PaginaDados <> nil) and (PageID = PaginaDados.ID) then
    Result := GetPreviousData('PastaDados', '') <> '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Motivo: String;
begin
  Result := True;
  if (PaginaDados = nil) or (CurPageID <> PaginaDados.ID) then
    Exit;
  if not DadosValidos(RemoveBackslash(Trim(PaginaDados.Values[0])), Motivo) then
  begin
    MsgBox(Motivo, mbError, MB_OK);
    Result := False;
  end
  else if Pos('onedrive', Lowercase(PaginaDados.Values[0])) > 0 then
    Result := MsgBox('Esta pasta fica no OneDrive: os vídeos (grandes) seriam enviados para a nuvem e podem lotar o seu espaço lá. Usar mesmo assim?',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

// Antes de tocar em qualquer arquivo: a pasta de dados serve? O programa está fechado?
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Motivo: String;
begin
  if not DadosValidos(PastaDados, Motivo) then
  begin
    Log(Motivo);
    Result := Motivo;
    Exit;
  end;
  Result := ConflitoComDadosRegistrados(PastaDados);
  if Result <> '' then
  begin
    Log(Result);
    Exit;
  end;
  Result := EsperarProgramaFechado('instalar ou atualizar');
end;

// ---- execução dos passos ----
procedure MostrarPasso(const S: String);
var
  Barra, Fecha, N, Total: Integer;
begin
  PaginaAndamento.SetText(EtapaAtual, S);
  Barra := Pos('/', S);
  Fecha := Pos(']', S);
  if (Barra > 2) and (Fecha > Barra) then
  begin
    N := StrToIntDef(Copy(S, 2, Barra - 2), 0);
    Total := StrToIntDef(Copy(S, Barra + 1, Fecha - Barra - 1), 0);
    if (N > 0) and (Total >= N) then
      PaginaAndamento.SetProgress(N, Total);
  end;
end;

// saída dos programas: vai ao log; as linhas [n/N] viram o texto de andamento; as AVISO: são mostradas no fim
procedure SaidaDoComando(const S: String; const Error, FirstLine: Boolean);
begin
  Registrar('    ' + S);
  if Trim(S) = '' then
    Exit;
  UltimaLinha := Trim(S);       // o erro detalhado (stderr) também serve para a mensagem de falha
  if Error then
    Exit;
  if S[1] = '[' then
    MostrarPasso(S)
  else if Pos('AVISO:', S) = 1 then
    Avisos := Avisos + S + #13#10;
end;

function Executar(const Rotulo, Programa, Args, Pasta: String): Integer;
var
  Codigo: Integer;
begin
  Registrar('passo: ' + Rotulo + ' | ' + Programa + ' ' + Args);
  UltimaLinha := '';
  if not ExecAndLogOutput(Programa, Args, Pasta, SW_HIDE, ewWaitUntilTerminated, Codigo, @SaidaDoComando) then
    RaiseException(Rotulo + ': não consegui abrir ' + Programa + ' (' + SysErrorMessage(Codigo) + ')');
  Registrar('passo: ' + Rotulo + ' terminou com código ' + IntToStr(Codigo));
  Result := Codigo;
end;

procedure Rodar(const Rotulo, Programa, Args, Pasta: String);
begin
  if Executar(Rotulo, Programa, Args, Pasta) <> 0 then
    RaiseException(Rotulo + ' falhou. Última mensagem: ' + UltimaLinha);
end;

procedure Etapa(const Numero: Integer; const Texto: String);
begin
  EtapaAtual := Texto;
  PaginaAndamento.SetText(Texto, '');
  PaginaAndamento.SetProgress(Numero, ETAPAS);
  Registrar('== ' + Texto);
end;

procedure PrepararAmbiente(const App, Dados: String);
begin
  // uv: Python gerenciado DENTRO do programa (sem registro do Windows, sem %APPDATA%\uv) e cache dentro do programa (prova A)
  DefinirVariavel('UV_PYTHON_INSTALL_DIR', App + '\runtime');
  DefinirVariavel('UV_CACHE_DIR', App + '\cache-uv');
  DefinirVariavel('UV_NO_CONFIG', '1');
  // Python: saída em UTF-8 (acento e apóstrofo chegam certos ao log) e nada do Python da pessoa (vazio = sem efeito)
  DefinirVariavel('PYTHONIOENCODING', 'utf-8');
  DefinirVariavel('PYTHONUTF8', '1');
  DefinirVariavel('PYTHONHOME', '');
  DefinirVariavel('PYTHONPATH', '');
  DefinirVariavel('PYTHONNOUSERSITE', '1');
  DefinirVariavel('HF_HUB_DISABLE_TELEMETRY', '1');
  DefinirVariavel('BISTURI_DADOS', Dados);
end;

function OpcoesUv: String;
begin
  Result := ' --no-config --no-progress --color never --cache-dir ' + Aspas(ExpandConstant('{app}') + '\cache-uv');
end;

// sem contar com avaliação curta do "and": cada conferência num if
function FFmpegInstalado: Boolean;
var
  Bin: String;
  Linhas: TArrayOfString;
begin
  Result := False;
  Bin := ExpandConstant('{app}\app\bin');
  if FileExists(Bin + '\ffmpeg.exe') and FileExists(Bin + '\ffprobe.exe') then
    if LoadStringsFromFile(Bin + '\ffmpeg.sha256', Linhas) then
      if GetArrayLength(Linhas) > 0 then
        Result := Lowercase(Trim(Linhas[0])) = Lowercase('{#FFmpegSha256}');
end;

function PrecisaBaixarFFmpeg: Boolean;
begin
  Result := not FFmpegInstalado;
end;

// a pasta extraída que tem bin\ffmpeg.exe (ex.: {tmp}\ffx\ffmpeg-9.1-essentials_build)
function PastaExtraidaFFmpeg: String;
var
  Base: String;
  Busca: TFindRec;
begin
  Result := '';
  Base := ExpandConstant('{tmp}\ffx');
  if FileExists(Base + '\bin\ffmpeg.exe') then
    Result := Base
  else if FindFirst(Base + '\*', Busca) then
  begin
    try
      repeat
        if FileExists(Base + '\' + Busca.Name + '\bin\ffmpeg.exe') then
          Result := Base + '\' + Busca.Name;
      until (Result <> '') or not FindNext(Busca);
    finally
      FindClose(Busca);
    end;
  end;
end;

procedure PassoFFmpeg;
var
  Bin, Origem: String;
  Linhas: TArrayOfString;
begin
  if FFmpegInstalado then
  begin
    Registrar('FFmpeg já instalado com o mesmo hash');
    Exit;
  end;
  Origem := PastaExtraidaFFmpeg;
  if Origem = '' then
    RaiseException('não achei o ffmpeg.exe no arquivo baixado');
  Bin := ExpandConstant('{app}\app\bin');
  ForceDirectories(Bin);
  // só ffmpeg.exe e ffprobe.exe; o ffplay.exe e o resto ficam no {tmp} e somem
  if not CopyFile(Origem + '\bin\ffmpeg.exe', Bin + '\ffmpeg.exe', False) then
    RaiseException('não consegui copiar o ffmpeg.exe');
  if not CopyFile(Origem + '\bin\ffprobe.exe', Bin + '\ffprobe.exe', False) then
    RaiseException('não consegui copiar o ffprobe.exe');
  SetArrayLength(Linhas, 1);
  Linhas[0] := Lowercase('{#FFmpegSha256}');
  if not SaveStringsToUTF8File(Bin + '\ffmpeg.sha256', Linhas, False) then
    RaiseException('não consegui gravar o ffmpeg.sha256');
end;

procedure PassoPos(const App, Dados, Python: String);
var
  Args: String;
  Codigo: Integer;
begin
  Args := '-X utf8 -m cs2clip.instalacao pos --dados ' + Aspas(Dados) + ' --bin ' + Aspas(App + '\app\bin');
  if UsarNvidia then
    Args := Args + ' --nvidia'
  else
    Args := Args + ' --sem-nvidia';
  if QuerModelo then
    Args := Args + ' --com-modelo';
  if WizardIsTaskSelected('demo') then
    Args := Args + ' --com-gravacao-demo';
  if not WizardIsTaskSelected('musicas') then
    Args := Args + ' --sem-musicas';
  Codigo := Executar('preparar o programa', Python, Args, App + '\app');
  if Codigo = 1 then
    RaiseException('um passo essencial da preparação falhou: ' + UltimaLinha)
  else if Codigo = 2 then
    RaiseException('a preparação recusou a pasta de dados ou outro argumento (código 2): ' + UltimaLinha)
  else if Codigo <> 0 then
    RaiseException('a preparação terminou com o código ' + IntToStr(Codigo) + ': ' + UltimaLinha);
end;

procedure PassosFinaisCorpo;
var
  App, Dados, Uv, Python, Args, Motivo: String;
begin
  App := ExpandConstant('{app}');
  Dados := PastaDados;
  Registrar('=== Bisturi Clips {#AppVersion}: instalando em ' + App + '; dados em ' + Dados);
  if not DadosValidos(Dados, Motivo) then
    RaiseException(Motivo);
  if Pos('onedrive', Lowercase(Dados)) > 0 then
    Registrar('AVISO: a pasta de dados fica no OneDrive');
  if not ForceDirectories(Dados) then
    RaiseException('não consegui criar a pasta de dados ' + Dados);
  GravarPastaDados(Dados);
  PrepararAmbiente(App, Dados);
  Uv := App + '\uv.exe';
  Python := App + '\venv\Scripts\python.exe';

  Etapa(1, 'Instalando o FFmpeg...');
  PassoFFmpeg;

  Etapa(2, 'Instalando o Python do programa...');
  Args := 'python install 3.12 --install-dir ' + Aspas(App + '\runtime') + ' --no-bin --no-registry' + OpcoesUv;
  Rodar('instalar o Python', Uv, Args, App);
  Args := 'venv ' + Aspas(App + '\venv') + ' --python 3.12 --managed-python --no-python-downloads --allow-existing' + OpcoesUv;
  Rodar('criar o ambiente do programa', Uv, Args, App);

  Etapa(3, 'Instalando os pacotes do programa (conferidos por hash)...');
  Args := 'pip sync --python ' + Aspas(Python) + ' --require-hashes --compile-bytecode --no-python-downloads' + OpcoesUv;
  Args := Args + ' ' + Aspas(App + '\app\requirements-runtime.lock');
  if UsarNvidia then
    Args := Args + ' ' + Aspas(App + '\app\requirements-gpu.lock');
  Rodar('instalar os pacotes', Uv, Args, App);

  Etapa(4, 'Preparando a pasta de dados, as músicas e o modelo de voz...');
  PassoPos(App, Dados, Python);

  // o cache do uv só servia à instalação (o venv usa ligações físicas: apagar não desfaz nada); numa falha ele fica e acelera a retomada
  DelTree(App + '\cache-uv', True, True, True);
  Registrar('=== instalação concluída');
end;

procedure PassosFinais;
begin
  PassosFalharam := False;
  Avisos := '';
  try
    try
      PaginaAndamento.Show;    // dentro do try: uma exceção aqui também vira PassosFalharam (código 4), nunca um Setup "pronto"
      PassosFinaisCorpo;
    except
      PassosFalharam := True;
      MensagemFalha := 'A instalação não terminou: ' + GetExceptionMessage + #13#10#13#10
        + 'O que aconteceu está em ' + ArquivoDeLog + '.' + #13#10
        + 'Abra o instalador de novo para continuar: ele retoma de onde parou e não apaga os seus dados.';
      Registrar('FALHA: ' + GetExceptionMessage);
      RemoverAtalhos;
      SuppressibleMsgBox(MensagemFalha, mbCriticalError, MB_OK, IDOK);
    end;
  finally
    DelTree(ExpandConstant('{tmp}\ffx'), True, True, True);
    PaginaAndamento.Hide;
  end;
  if (not PassosFalharam) and (Avisos <> '') then
    SuppressibleMsgBox('O Bisturi Clips foi instalado, com um aviso:' + #13#10#13#10 + Avisos + #13#10
      + 'O modelo de voz pode ser baixado depois, na tela Início do programa.', mbInformation, MB_OK, IDOK);
end;

// ssPostInstall vem depois do [Icons]: só aqui se sabe que os atalhos desta instalação existem (numa falha o [Icons] não os cria)
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not PassosFalharam) then
    GravarAtalhosCriados;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and PassosFalharam then
  begin
    WizardForm.FinishedHeadingLabel.Caption := 'A instalação não terminou';
    WizardForm.FinishedLabel.Caption := MensagemFalha;
  end;
end;

// O ExitProcess pula a limpeza do Setup e o .7z baixado (36 MB) ficava em %TEMP%\is-*.tmp (P9, F4): some só o que ESTE .iss pôs no
// {tmp} (o arquivo baixado do [Files], com o DestName dele, e a pasta de extração); o resto do {tmp} é do Inno.
procedure LimparTemporariosDoFFmpeg;
begin
  try
    DeleteFile(ExpandConstant('{tmp}\ffmpeg.7z'));
    DelTree(ExpandConstant('{tmp}\ffx'), True, True, True);
  except
    Log('não consegui limpar o FFmpeg baixado no temporário: ' + GetExceptionMessage);
  end;
end;

procedure DeinitializeSetup;
begin
  if PassosFalharam then
  begin
    Log('saindo com o código 4 (passos finais falharam)');
    LimparTemporariosDoFFmpeg;
    ExitProcess(4);
  end;
end;

// ---- desinstalador ----
function InitializeUninstall: Boolean;
begin
  Result := EsperarProgramaFechado('desinstalar') = '';
end;

// só apaga algo de uma pasta que o pos marcou ao prepará-la (ele não marca uma pasta que já tinha coisas da pessoa com os nomes do
// programa) e cujo caminho tem o formato certo (nunca a raiz do disco, nem por "D:\." ou "D:\x\..")
function PastaDadosSegura(const Dados: String): Boolean;
begin
  Result := False;
  if ProblemaNoCaminho(Dados) = '' then
    if DirExists(Dados) then
      Result := FileExists(Dados + '\' + MARCADOR_DADOS);
end;

procedure ApagarItem(const Caminho: String);
begin
  if DirExists(Caminho) then
    DelTree(Caminho, True, True, True)
  else if FileExists(Caminho) then
    DeleteFile(Caminho);
end;

// Nunca a pasta inteira (a pessoa pode ter escolhido uma pasta com outras coisas): só o que o programa cria nela.
procedure ApagarDados(const Dados: String; const Tudo: Boolean);
begin
  ApagarItem(Dados + '\' + 'modelos');
  if not Tudo then
    Exit;
  ApagarItem(Dados + '\' + 'config.toml');
  ApagarItem(Dados + '\' + 'config.toml.bak');
  ApagarItem(Dados + '\' + 'config.toml.bak-antes-dos-padroes');
  ApagarItem(Dados + '\' + 'entrada');
  ApagarItem(Dados + '\' + 'saida');
  ApagarItem(Dados + '\' + 'demos');
  ApagarItem(Dados + '\' + 'gravacao_demo');
  ApagarItem(Dados + '\' + 'trabalhos');
  ApagarItem(Dados + '\' + 'dados');
  ApagarItem(Dados + '\' + 'musicas');
  ApagarItem(Dados + '\' + 'logs');
  ApagarItem(Dados + '\' + 'cache');
  ApagarItem(Dados + '\' + MARCADOR_DADOS);    // por último: se algo acima falhar, a pasta continua marcada
  RemoveDir(Dados);    // só some se ficou vazia
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    // decide ANTES de o programa sumir (o pasta_dados.txt mora nele); apaga só no fim
    DadosDesinstalar := DadosRegistrados;
    ApagarTudo := False;
    ApagarModelo := False;
    Log('desinstalador: dados=[' + DadosDesinstalar + '] silencioso=' + IntToStr(Ord(UninstallSilent)));
    if (DadosDesinstalar = '') or not DirExists(DadosDesinstalar) then
      Exit;
    // sem o marcador nada é apagado (nem com /APAGARDADOS=1): o desinstalador não sabe o que é da pessoa e o que é do programa
    if not PastaDadosSegura(DadosDesinstalar) then
    begin
      Log('pasta de dados sem o marcador ' + MARCADOR_DADOS + ' (ou com caminho fora do formato): nada dela será apagado');
      if not UninstallSilent then
        MsgBox('Os seus dados em ' + DadosDesinstalar + ' ficam onde estão: a pasta não tem o marcador do Bisturi Clips (' + MARCADOR_DADOS
          + '), então o desinstalador não apaga nada dela. Se quiser, apague à mão o que for do programa.', mbInformation, MB_OK);
      Exit;
    end;
    if UninstallSilent then
      ApagarTudo := Param('APAGARDADOS', '0') = '1'   // silencioso: só apaga se pedido
    else
    begin
      ApagarTudo := MsgBox('Apagar também os dados que o Bisturi Clips criou em ' + DadosDesinstalar + '?' + #13#10#13#10
        + 'Isto apaga só o que é do programa: a configuração, os vídeos de entrada, os kits prontos, as gravações e o modelo de voz. '
        + 'Outras coisas que você guardou nessa pasta ficam. Não dá para desfazer.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
      if (not ApagarTudo) and DirExists(DadosDesinstalar + '\modelos') then
        ApagarModelo := MsgBox('Apagar só o modelo de voz (até 1,6 GB, em ' + DadosDesinstalar + '\modelos)? Os seus vídeos e kits ficam.',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    end;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    if ApagarTudo and PastaDadosSegura(DadosDesinstalar) then
    begin
      Log('apagando os dados do programa em ' + DadosDesinstalar);
      ApagarDados(DadosDesinstalar, True);
    end
    else if ApagarModelo and PastaDadosSegura(DadosDesinstalar) then
    begin
      Log('apagando o modelo de voz');
      ApagarDados(DadosDesinstalar, False);
    end
    else
      Log('pasta de dados mantida');
  end;
end;
