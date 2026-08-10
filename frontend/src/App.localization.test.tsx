import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { DICTIONARY_LANGUAGE_PREFERENCE_KEY } from "./lib/dictionaryLanguage";
import { STUDY_LANGUAGE_PREFERENCE_KEY } from "./lib/studyLanguage";
import { UI_LOCALE_PREFERENCE_KEY } from "./lib/uiLocale";

describe("App UI localization", () => {
  afterEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("defaults fresh browser state to English without changing language-axis defaults", () => {
    window.localStorage.clear();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Read Japanese in context" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
    expect(screen.getByRole("combobox", { name: "Study language" })).toHaveValue("pt-BR");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY)).toBeNull();
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBeNull();
  });

  it("switches UI and study languages without rewriting dictionary preference", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "de");
    const first = render(<App />);

    await user.selectOptions(screen.getByRole("combobox", { name: "Study language" }), "en");
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "pt-BR");

    expect(screen.getByRole("heading", { name: "Leia japonês no contexto" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    expect(window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY)).toBe("pt-BR");
    expect(window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("de");
    expect(document.documentElement).toHaveAttribute("lang", "pt-BR");

    first.unmount();
    render(<App />);

    expect(screen.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("de");
    expect(document.documentElement).toHaveAttribute("lang", "pt-BR");
  });

  it("falls back to English for an invalid persisted UI locale without changing dictionary preference", () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "es");
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "pt-BR");
    render(<App />);

    expect(screen.getByRole("heading", { name: "Read Japanese in context" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("pt-BR");
    expect(document.documentElement).toHaveAttribute("lang", "en");
  });
});
