import type { DocumentAggregateStatus, JobStatus } from "./api";
import type { UiLocale } from "./uiLocale";

export interface DocumentUiMessages {
  readonly selectedPages: (count: number) => string;
  readonly movePageUp: (name: string) => string;
  readonly movePageDown: (name: string) => string;
  readonly removePage: (name: string) => string;
  readonly clearPages: string;
  readonly pageOrderHint: string;
  readonly analyzePages: (count: number) => string;
  readonly pageOf: (current: number, total: number) => string;
  readonly previousPage: string;
  readonly nextPage: string;
  readonly documentNavigation: string;
  readonly documentProgress: (
    completed: number,
    total: number,
    processing: number,
    failed: number,
    cancelled: number,
  ) => string;
  readonly aggregateStatus: (status: DocumentAggregateStatus) => string;
  readonly processingPage: string;
  readonly failedPage: string;
  readonly cancelledPage: string;
  readonly readablePage: string;
  readonly pageStatus: (page: number, status: JobStatus, readable: boolean) => string;
  readonly retryFailedPages: string;
  readonly retryingFailedPages: string;
  readonly cancelProcessing: string;
  readonly cancellingProcessing: string;
  readonly moveCurrentPageEarlier: string;
  readonly moveCurrentPageLater: string;
  readonly actionFailed: string;
  readonly documentUploadFailed: string;
  readonly documentPageLimit: string;
  readonly documentByteLimit: string;
  readonly documentPixelLimit: string;
  readonly documentReloadNote: string;
}

const en: DocumentUiMessages = {
  selectedPages: (count) => `${count} ${count === 1 ? "page" : "pages"} selected`,
  movePageUp: (name) => `Move ${name} earlier`,
  movePageDown: (name) => `Move ${name} later`,
  removePage: (name) => `Remove ${name}`,
  clearPages: "Clear selection",
  pageOrderHint: "The displayed order is the upload order. Use the controls to change it before analysis.",
  analyzePages: (count) => count <= 1 ? "Analyze page" : `Analyze ${count} pages`,
  pageOf: (current, total) => `Page ${current} of ${total}`,
  previousPage: "Previous",
  nextPage: "Next",
  documentNavigation: "Document pages",
  documentProgress: (completed, total, processing, failed, cancelled) =>
    `${completed} / ${total} pages readable · ${processing} processing · ${failed} failed · ${cancelled} cancelled`,
  aggregateStatus: (status) => ({
    processing: "Document processing",
    completed: "Document complete",
    completed_with_errors: "Document complete with errors",
    cancelled: "Document processing cancelled",
  })[status],
  processingPage: "Processing",
  failedPage: "Failed",
  cancelledPage: "Cancelled",
  readablePage: "Readable",
  pageStatus: (page, status, readable) => `Page ${page}: ${readable ? "readable" : status.replaceAll("_", " ")}`,
  retryFailedPages: "Retry failed pages",
  retryingFailedPages: "Retrying failed pages…",
  cancelProcessing: "Cancel processing",
  cancellingProcessing: "Cancelling processing…",
  moveCurrentPageEarlier: "Move page earlier",
  moveCurrentPageLater: "Move page later",
  actionFailed: "The document operation could not be completed.",
  documentUploadFailed: "The document could not be created.",
  documentPageLimit: "This document contains too many pages.",
  documentByteLimit: "The combined image size exceeds the document limit.",
  documentPixelLimit: "The combined image resolution exceeds the document limit.",
  documentReloadNote: "Document access is kept only in this active page session; capability tokens are never placed in the URL.",
};

const ptBR: DocumentUiMessages = {
  selectedPages: (count) => `${count} ${count === 1 ? "página selecionada" : "páginas selecionadas"}`,
  movePageUp: (name) => `Mover ${name} para cima`,
  movePageDown: (name) => `Mover ${name} para baixo`,
  removePage: (name) => `Remover ${name}`,
  clearPages: "Limpar seleção",
  pageOrderHint: "A ordem exibida será a ordem do envio. Use os controles para alterá-la antes da análise.",
  analyzePages: (count) => count <= 1 ? "Analisar página" : `Analisar ${count} páginas`,
  pageOf: (current, total) => `Página ${current} de ${total}`,
  previousPage: "Anterior",
  nextPage: "Próxima",
  documentNavigation: "Páginas do documento",
  documentProgress: (completed, total, processing, failed, cancelled) =>
    `${completed} / ${total} páginas disponíveis · ${processing} processando · ${failed} falharam · ${cancelled} canceladas`,
  aggregateStatus: (status) => ({
    processing: "Documento em processamento",
    completed: "Documento concluído",
    completed_with_errors: "Documento concluído com erros",
    cancelled: "Processamento do documento cancelado",
  })[status],
  processingPage: "Processando",
  failedPage: "Falhou",
  cancelledPage: "Cancelada",
  readablePage: "Disponível",
  pageStatus: (page, status, readable) => `Página ${page}: ${readable ? "disponível" : status.replaceAll("_", " ")}`,
  retryFailedPages: "Tentar páginas com falha novamente",
  retryingFailedPages: "Tentando páginas com falha novamente…",
  cancelProcessing: "Cancelar processamento",
  cancellingProcessing: "Cancelando processamento…",
  moveCurrentPageEarlier: "Mover página para antes",
  moveCurrentPageLater: "Mover página para depois",
  actionFailed: "Não foi possível concluir a operação do documento.",
  documentUploadFailed: "Não foi possível criar o documento.",
  documentPageLimit: "Este documento contém páginas demais.",
  documentByteLimit: "O tamanho combinado das imagens excede o limite do documento.",
  documentPixelLimit: "A resolução combinada das imagens excede o limite do documento.",
  documentReloadNote: "O acesso ao documento existe somente nesta sessão ativa da página; capability tokens nunca são colocados na URL.",
};

export function documentMessagesFor(locale: UiLocale): DocumentUiMessages {
  return locale === "pt-BR" ? ptBR : en;
}
