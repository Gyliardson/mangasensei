import "@testing-library/jest-dom/vitest";

beforeEach(() => {
  window.localStorage.setItem("mangasensei.ui.locale", "pt-BR");
});

afterEach(() => {
  document.body.innerHTML = "";
});
