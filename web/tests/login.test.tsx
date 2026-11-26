/**
 * Login form tests: the fixture variant swaps the credential form for an
 * honest demo-entry action; the Supabase variant renders the email/password
 * fields with typed error states. Both are bilingual — asserted via the
 * zh dictionary as well.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "../components/login-form";
import { MESSAGES } from "../lib/messages";

vi.mock("../lib/auth-actions", () => ({
  signInAction: vi.fn(),
  fixtureEnterAction: vi.fn(),
  changePasswordAction: vi.fn(),
}));

describe("LoginForm", () => {
  it("fixture mode: enter action instead of credentials, no password fields", () => {
    const html = renderToString(
      <LoginForm fixture next="/feed" labels={MESSAGES.en.login} />,
    );
    expect(html).toContain("Enter (fixture mode)");
    expect(html).toContain('name="next" value="/feed"');
    expect(html).not.toContain('type="password"');
    expect(html).toContain("Fixture mode: demo data, no live sign-in.");
  });

  it("fixture mode renders in Chinese too", () => {
    const html = renderToString(
      <LoginForm fixture next="/feed" labels={MESSAGES.zh.login} />,
    );
    expect(html).toContain("进入（演示模式）");
    expect(html).toContain("未连接真实登录");
  });

  it("Supabase mode: email + password fields with a submit button", () => {
    const html = renderToString(
      <LoginForm fixture={false} next="/themes" labels={MESSAGES.en.login} />,
    );
    expect(html).toContain('type="email"');
    expect(html).toContain('type="password"');
    expect(html).toContain("Sign in");
    expect(html).toContain('name="next" value="/themes"');
    // Idle state renders no error region.
    expect(html).not.toContain("role=\"alert\"");
  });

  it("zh dictionary carries every login label the form can render", () => {
    const labels = MESSAGES.zh.login;
    for (const key of [
      "title",
      "email",
      "password",
      "submit",
      "submitting",
      "invalid",
      "service",
      "retry",
      "fixtureNote",
      "fixtureEnter",
    ] as const) {
      expect(typeof labels[key], key).toBe("string");
      expect(labels[key].length, key).toBeGreaterThan(0);
    }
  });
});
