import type { JobStatus } from "./api";
import type { DictionaryLanguage } from "./dictionaryLanguage";
import {
  documentMessagesFor,
  type DocumentUiMessages,
} from "./documentUiMessages";
import type { StudyLanguage } from "./studyLanguage";
import type { UiLocale } from "./uiLocale";

export interface UiMessages extends DocumentUiMessages {
  readonly documentDescription: string;
  readonly skipLink: string;
  readonly uiLocaleLabel: string;
  readonly localeNames: Readonly<Record<UiLocale, string>>;
  readonly brandHomeAria: string;
  readonly privacyNote: string;
  readonly introEyebrow: string;
  readonly introTitle: string;
  readonly introLede: string;
  readonly uploadTitle: string;
  readonly uploadRequirements: string;
  readonly studyLanguageLabel: string;
  readonly studyLanguageNote: string;
  readonly studyLanguageName: (language: StudyLanguage) => string;
  readonly dictionaryLanguageLabel: string;
  readonly dictionaryLanguageName: (language: DictionaryLanguage | "de") => string;
  readonly selectImage: string;
  readonly fileDropHint: string;
  readonly pageImageAria: string;
  readonly analyzePage: string;
  readonly uploadingImage: string;
  readonly stopFollowing: string;
  readonly uploadRetention: string;
  readonly previewEyebrow: string;
  readonly previewTitle: string;
  readonly previewBody: string;
  readonly oneImageOnly: string;
  readonly selectImageFirst: string;
  readonly unexpectedProcessingError: string;
  readonly studyLanguageUpdateFailed: string;
  readonly dictionaryLanguageUpdateFailed: string;
  readonly jobStatus: (status: JobStatus | null) => string;
  readonly apiError: (code: string) => string;
  readonly processedPage: string;
  readonly selectRegion: string;
  readonly studyPreferences: string;
  readonly furiganaReading: string;
  readonly furiganaHiragana: string;
  readonly furiganaKatakana: string;
  readonly furiganaHidden: string;
  readonly navigation: string;
  readonly newPage: string;
  readonly updatingStudyLanguage: (target: string, current: string) => string;
  readonly retainedStudyLanguage: (current: string) => string;
  readonly updatingDictionaryLanguage: (target: string, current: string) => string;
  readonly retainedDictionaryLanguage: (current: string) => string;
  readonly pagePresentation: string;
  readonly pageFit: string;
  readonly fitWidth: string;
  readonly fitPage: string;
  readonly fitComfortable: string;
  readonly pageZoom: string;
  readonly decreaseZoom: string;
  readonly increaseZoom: string;
  readonly zoomLevel: string;
  readonly horizontalPan: string;
  readonly originalPageAlt: string;
  readonly recognizedRegions: string;
  readonly regionAria: (index: number, text: string) => string;
  readonly readerMeta: (
    regionCount: number,
    studyLanguage: string,
    detector: string,
    recognizer: string,
  ) => string;
  readonly studyPanel: string;
  readonly noRecognizedRegions: string;
  readonly confidence: (percent: number) => string;
  readonly contextualTranslation: string;
  readonly contextualUnavailable: string;
  readonly vocabulary: string;
  readonly dictionaryRequestedNote: (language: string) => string;
  readonly englishFallbackBadge: string;
  readonly unsupportedPortugueseDictionary: string;
  readonly dictionaryProvenance: (dataset: string, language: string) => string;
  readonly noDictionaryMatch: string;
  readonly grammar: string;
  readonly noGrammarPoint: string;
  readonly unofficialJlpt: (level: string) => string;
}

const englishApiErrors: Readonly<Record<string, string>> = {
  invalid_image: "Use a JPEG, PNG, or WebP image.",
  image_too_large: "The image must be no larger than 12 MiB.",
  image_unavailable: "The protected image could not be loaded.",
  expired: "This page has expired.",
  failed: "Page processing failed.",
  processing_failed: "Page processing failed.",
  analysis_in_progress: "Another page analysis is already in progress.",
  request_failed: "The request could not be completed.",
};

const portugueseApiErrors: Readonly<Record<string, string>> = {
  invalid_image: "Use uma imagem JPEG, PNG ou WebP.",
  image_too_large: "A imagem deve ter no máximo 12 MiB.",
  image_unavailable: "A imagem protegida não pôde ser carregada.",
  expired: "Esta página expirou.",
  failed: "O processamento da página falhou.",
  processing_failed: "O processamento da página falhou.",
  analysis_in_progress: "Já existe outra análise desta página em andamento.",
  request_failed: "A requisição não pôde ser concluída.",
};

const en: UiMessages = {
  ...documentMessagesFor("en"),
  documentDescription: "Study Japanese directly in the context of the manga pages you are reading.",
  skipLink: "Skip to content",
  uiLocaleLabel: "Interface language",
  localeNames: { en: "English", "pt-BR": "Português (Brasil)" },
  brandHomeAria: "MangaSensei, home",
  privacyNote: "Local, temporary processing",
  introEyebrow: "Assisted reading without altering the page",
  introTitle: "Read Japanese in context",
  introLede: "Upload a page, select a region, and turn each line into study material.",
  uploadTitle: "Choose a page",
  uploadRequirements: "JPEG, PNG, or WebP. Up to 12 MiB and 25 megapixels.",
  studyLanguageLabel: "Study language",
  studyLanguageNote: "Controls contextual explanations; the analyzed content remains Japanese.",
  studyLanguageName: (language) => language === "en" ? "English" : "Portuguese (Brazil)",
  dictionaryLanguageLabel: "Dictionary language",
  dictionaryLanguageName: (language) => {
    if (language === "de") return "German";
    if (language === "pt-BR") return "Portuguese (Brazil)";
    return "English";
  },
  selectImage: "Select image",
  fileDropHint: "or drag the file here",
  pageImageAria: "Page image",
  analyzePage: "Analyze page",
  uploadingImage: "Uploading image",
  stopFollowing: "Stop following",
  uploadRetention: "Stopping only ends the wait on this screen; analysis may continue. Originals and results are automatically deleted after 24 hours.",
  previewEyebrow: "Next step",
  previewTitle: "Text, reading, and nuance side by side",
  previewBody: "Recognized regions will appear over the original image and can be opened from the keyboard.",
  oneImageOnly: "Drop only one image at a time.",
  selectImageFirst: "Select an image before continuing.",
  unexpectedProcessingError: "Processing could not be completed.",
  studyLanguageUpdateFailed: "The study language could not be updated.",
  dictionaryLanguageUpdateFailed: "The dictionary language could not be updated.",
  jobStatus: (status) => {
    const labels: Partial<Record<JobStatus, string>> = {
      pending: "Waiting for worker",
      claimed: "Preparing analysis",
      processing_ocr: "Recognizing text",
      processing_linguistics: "Analyzing Japanese",
      processing_gemini: "Generating context",
      retryable_failure: "Trying again",
    };
    return status ? labels[status] ?? "Processing page" : "Processing page";
  },
  apiError: (code) => englishApiErrors[code] ?? "The request could not be completed.",
  processedPage: "Processed page",
  selectRegion: "Select a region",
  studyPreferences: "Study preferences",
  furiganaReading: "Furigana display",
  furiganaHiragana: "Hiragana",
  furiganaKatakana: "Katakana",
  furiganaHidden: "Hidden",
  navigation: "Navigation",
  newPage: "New page",
  updatingStudyLanguage: (target, current) => `Updating explanations to ${target}. The displayed result remains in ${current} until the new analysis finishes.`,
  retainedStudyLanguage: (current) => `The result in ${current} was kept.`,
  updatingDictionaryLanguage: (target, current) => `Updating dictionary meanings to ${target}. The completed ${current} dictionary result remains visible until reprojection finishes.`,
  retainedDictionaryLanguage: (current) => `The completed dictionary result in ${current} was kept.`,
  pagePresentation: "Page presentation",
  pageFit: "Page fit",
  fitWidth: "Width",
  fitPage: "Full page",
  fitComfortable: "Comfortable",
  pageZoom: "Page zoom",
  decreaseZoom: "Zoom out",
  increaseZoom: "Zoom in",
  zoomLevel: "Zoom level",
  horizontalPan: "Page view with horizontal scrolling",
  originalPageAlt: "Original page uploaded for study",
  recognizedRegions: "Recognized text regions",
  regionAria: (index, text) => `Region ${index}: ${text}`,
  readerMeta: (regionCount, studyLanguage, detector, recognizer) => `${regionCount} ${regionCount === 1 ? "region" : "regions"} · study ${studyLanguage} · OCR ${detector}/${recognizer} · deletion after 24 hours`,
  studyPanel: "Study panel",
  noRecognizedRegions: "No text region was recognized on this page.",
  confidence: (percent) => `${percent}% confidence`,
  contextualTranslation: "Contextual translation",
  contextualUnavailable: "Contextual analysis unavailable.",
  vocabulary: "Vocabulary",
  dictionaryRequestedNote: (language) => `Requested dictionary: ${language}`,
  englishFallbackBadge: "English fallback",
  unsupportedPortugueseDictionary: "Deterministic Portuguese JMdict glosses are not available. The request remains Portuguese (Brazil), while meanings are supplied by the reviewed English JMdict fallback.",
  dictionaryProvenance: (dataset, language) => `${dataset} · ${language}`,
  noDictionaryMatch: "No reliable dictionary association.",
  grammar: "Grammar",
  noGrammarPoint: "No additional grammar point.",
  unofficialJlpt: (level) => `JLPT ${level} unofficial`,
};

const ptBR: UiMessages = {
  ...documentMessagesFor("pt-BR"),
  documentDescription: "Estude japonês diretamente no contexto das páginas que você está lendo.",
  skipLink: "Ir para o conteúdo",
  uiLocaleLabel: "Idioma da interface",
  localeNames: { en: "English", "pt-BR": "Português (Brasil)" },
  brandHomeAria: "MangaSensei, página inicial",
  privacyNote: "Processamento local e temporário",
  introEyebrow: "Leitura assistida, sem alterar a página",
  introTitle: "Leia japonês no contexto",
  introLede: "Envie uma página, selecione uma região e transforme cada fala em material de estudo.",
  uploadTitle: "Escolha uma página",
  uploadRequirements: "JPEG, PNG ou WebP. Até 12 MiB e 25 megapixels.",
  studyLanguageLabel: "Idioma de estudo",
  studyLanguageNote: "Define explicações contextuais; o conteúdo analisado continua japonês.",
  studyLanguageName: (language) => language === "en" ? "Inglês" : "Português (Brasil)",
  dictionaryLanguageLabel: "Idioma do dicionário",
  dictionaryLanguageName: (language) => {
    if (language === "de") return "Alemão";
    if (language === "pt-BR") return "Português (Brasil)";
    return "Inglês";
  },
  selectImage: "Selecionar imagem",
  fileDropHint: "ou arraste o arquivo para cá",
  pageImageAria: "Imagem da página",
  analyzePage: "Analisar página",
  uploadingImage: "Enviando imagem",
  stopFollowing: "Parar de acompanhar",
  uploadRetention: "Parar de acompanhar interrompe apenas a espera nesta tela; a análise pode continuar. Originais e resultados são excluídos automaticamente após 24 horas.",
  previewEyebrow: "Próxima etapa",
  previewTitle: "Texto, leitura e nuance lado a lado",
  previewBody: "As regiões reconhecidas aparecerão sobre a imagem original e poderão ser abertas por teclado.",
  oneImageOnly: "Solte apenas uma imagem por vez.",
  selectImageFirst: "Selecione uma imagem antes de continuar.",
  unexpectedProcessingError: "O processamento não pôde ser concluído.",
  studyLanguageUpdateFailed: "Não foi possível atualizar o idioma de estudo.",
  dictionaryLanguageUpdateFailed: "Não foi possível atualizar o idioma do dicionário.",
  jobStatus: (status) => {
    const labels: Partial<Record<JobStatus, string>> = {
      pending: "Aguardando worker",
      claimed: "Preparando análise",
      processing_ocr: "Reconhecendo texto",
      processing_linguistics: "Analisando japonês",
      processing_gemini: "Gerando contexto",
      retryable_failure: "Tentando novamente",
    };
    return status ? labels[status] ?? "Processando página" : "Processando página";
  },
  apiError: (code) => portugueseApiErrors[code] ?? "A requisição não pôde ser concluída.",
  processedPage: "Página processada",
  selectRegion: "Selecione uma região",
  studyPreferences: "Preferências de estudo",
  furiganaReading: "Exibição de furigana",
  furiganaHiragana: "Hiragana",
  furiganaKatakana: "Katakana",
  furiganaHidden: "Oculto",
  navigation: "Navegação",
  newPage: "Nova página",
  updatingStudyLanguage: (target, current) => `Atualizando explicações para ${target}. O resultado exibido continua em ${current} até a nova análise concluir.`,
  retainedStudyLanguage: (current) => `O resultado em ${current} foi mantido.`,
  updatingDictionaryLanguage: (target, current) => `Atualizando os significados do dicionário para ${target}. O resultado concluído em ${current} continua visível até a reprojeção terminar.`,
  retainedDictionaryLanguage: (current) => `O resultado concluído do dicionário em ${current} foi mantido.`,
  pagePresentation: "Apresentação da página",
  pageFit: "Ajuste da página",
  fitWidth: "Largura",
  fitPage: "Página inteira",
  fitComfortable: "Confortável",
  pageZoom: "Zoom da página",
  decreaseZoom: "Diminuir zoom",
  increaseZoom: "Aumentar zoom",
  zoomLevel: "Nível de zoom",
  horizontalPan: "Visualização da página com rolagem horizontal",
  originalPageAlt: "Página original enviada para estudo",
  recognizedRegions: "Regiões de texto reconhecidas",
  regionAria: (index, text) => `Região ${index}: ${text}`,
  readerMeta: (regionCount, studyLanguage, detector, recognizer) => `${regionCount} ${regionCount === 1 ? "região" : "regiões"} · estudo ${studyLanguage} · OCR ${detector}/${recognizer} · exclusão em 24 horas`,
  studyPanel: "Painel de estudo",
  noRecognizedRegions: "Nenhuma região de texto foi reconhecida nesta página.",
  confidence: (percent) => `${percent}% de confiança`,
  contextualTranslation: "Tradução contextual",
  contextualUnavailable: "Análise contextual indisponível.",
  vocabulary: "Vocabulário",
  dictionaryRequestedNote: (language) => `Dicionário solicitado: ${language}`,
  englishFallbackBadge: "Fallback em inglês",
  unsupportedPortugueseDictionary: "O JMdict determinístico não oferece glosas em português. A preferência solicitada continua Português (Brasil), mas os significados vêm do fallback revisado em inglês.",
  dictionaryProvenance: (dataset, language) => `${dataset} · ${language}`,
  noDictionaryMatch: "Nenhuma associação confiável ao dicionário.",
  grammar: "Gramática",
  noGrammarPoint: "Nenhum ponto gramatical adicional.",
  unofficialJlpt: (level) => `JLPT ${level} não oficial`,
};

const catalogs: Readonly<Record<UiLocale, UiMessages>> = { en, "pt-BR": ptBR };

export function messagesFor(locale: UiLocale): UiMessages {
  return catalogs[locale];
}
