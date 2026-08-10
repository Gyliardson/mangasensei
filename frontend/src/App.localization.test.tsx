import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";
import { STUDY_LANGUAGE_PREFERENCE_KEY } from "./lib/studyLanguage";
import { UI_LOCALE_PREFERENCE_KEY } from "./lib/uiLocale";

describe("App UI localization", () => {
  afterEach(() => {
    window.localStorage.clear();
    document.documentElement.lang = "en";
  });

  it("defaults fresh browser state to English without changing the study-language default", () => {
    window.localStorage.clear();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Read Japanese in context" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
    expect(screen.getByRole("combobox", { name: "Study language" })).toHaveValue("pt-BR");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY)).toBeNull();
  });

  it("switches to Brazilian Portuguese, persists it, and keeps study language independent", async () => {
    const user = userEvent.setup();
    window.localStorage.clear();
    const first = render(<App />);

    await user.selectOptions(screen.getByRole("combobox", { name: "Study language" }), "en");
    await user.selectOptions(screen.getByRole("combobox", { name: "Interface language" }), "pt-BR");

    expect(screen.getByRole("heading", { name: "Leia japonês no contexto" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    expect(window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY)).toBe("pt-BR");
    expect(window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
    expect(document.documentElement).toHaveAttribute("lang", "pt-BR");

    first.unmount();
    render(<App />);

    expect(screen.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    expect(document.documentElement).toHaveAttribute("lang", "pt-BR");
  });

  it("falls back to English for an invalid persisted UI locale", () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "es");
    render(<App />);

    expect(screen.getByRole("heading", { name: "Read Japanese in context" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
    expect(document.documentElement).toHaveAttribute("lang", "en");
  });
});
