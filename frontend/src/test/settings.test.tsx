import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRestClient, type EvaClient } from "../api/client";
import { createMockClient } from "../api/mock";
import fixtureSettings from "../../../contracts/fixtures/llm_settings_response.json";
import Settings from "../pages/Settings";
import type {
  LlmSettingsResponse,
  LlmSettingsUpdateRequest,
  LlmTestConnectionResponse,
  DetectModelsResponse,
} from "../api/types.generated";

const canonicalSettings = fixtureSettings as unknown as LlmSettingsResponse;

// One small explicit synthetic secret value used only to assert request
// handling; it is deliberately generic and never logged or snapshotted.
const TEST_KEY = "synthetic-test-key-value";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.doUnmock("../api/client");
  vi.resetModules();
});

function jsonResponse(body: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: true,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

describe("Settings screen", () => {
  it("loads the canonical sanitized fixture and renders provider/model/configured status", async () => {
    render(<Settings />);

    expect(await screen.findByText("openai_compatible")).toBeInTheDocument();
    // Form hydration happens in an effect after the data render — await the
    // hydrated values so the assertion is deterministic under any scheduler.
    await screen.findByDisplayValue("https://demo-host.tailnet.example:8321/v1");
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://demo-host.tailnet.example:8321/v1");
    expect(screen.getByLabelText("Model ID")).toHaveValue("demo-served-model-id");
    expect(screen.getByText("Configured")).toBeInTheDocument();
  });
  it("renders API key presence without ever rendering a secret value", async () => {
    render(<Settings />);

    await screen.findByText("openai_compatible");
    const apiKeyInput = screen.getByLabelText("API key (configured)") as HTMLInputElement;
    // Presence is communicated via label text; the input itself stays empty.
    expect(apiKeyInput).toHaveValue("");
    expect(apiKeyInput.type).toBe("password");
    expect(screen.getByText(/API key is configured/i)).toBeInTheDocument();
    // The canonical fixture contains no secret string, and none may be invented.
    expect(screen.queryByText(TEST_KEY)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sk-");
  });

  it("initial load failure remains failure with working retry", async () => {
    let fail = true;
    const flakyClient: EvaClient = {
      getLlmSettings: async () => {
        if (fail) throw new Error("settings down");
        return canonicalSettings;
      },
    } as unknown as EvaClient;

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => flakyClient };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);

    expect(await screen.findByText("Settings unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("openai_compatible")).not.toBeInTheDocument();

    // Retry actually retries with the same client
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("openai_compatible")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("renders the truthful unconfigured state when the backend says configured=false", async () => {
    const unconfigured: LlmSettingsResponse = { ...canonicalSettings, configured: false };
    // Stable client object — a per-call literal would change identity every
    // render and retrigger the query effect indefinitely.
    const unconfiguredClient = { getLlmSettings: async () => unconfigured } as unknown as EvaClient;
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => unconfiguredClient };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);

    expect(await screen.findByText("Not configured")).toBeInTheDocument();
    expect(screen.queryByText("Configured")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });
});

describe("Settings save (PUT /api/settings/llm)", () => {
  it("sends the exact PUT request; a blank API-key input is OMITTED, not sent as an empty string", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse(fixtureSettings));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    // No api_key field at all — blank inputs must be omitted, never sent as ""
    const response = await client.updateLlmSettings({ model: "new-model-id" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/settings/llm");
    expect(init.method).toBe("PUT");
    const body = JSON.parse(init.body) as LlmSettingsUpdateRequest;
    expect(body).toEqual({ model: "new-model-id" });
    expect("api_key" in body).toBe(false);
    expect(response.api_key_present).toBe(true);
  });

  it("sends a newly typed API key only when explicitly provided; sanitized response never echoes it", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse({ ...fixtureSettings, api_key_present: true }));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    await client.updateLlmSettings({ api_key: TEST_KEY });

    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(init.body) as LlmSettingsUpdateRequest;
    expect(body.api_key).toBe(TEST_KEY);
    // Sanitized response contains presence only
    const response = fixtureSettings as unknown as LlmSettingsResponse;
    expect(response.api_key_present).toBe(true);
    expect(JSON.stringify(response)).not.toContain(TEST_KEY);
  });

  it("password input clears after a successful save and shows the Saved confirmation", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "changed-model-id" } });
    fireEvent.change(screen.getByLabelText("API key (configured)"), { target: { value: TEST_KEY } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect((screen.getByLabelText("API key (configured)") as HTMLInputElement).value).toBe("");
    // The sanitized display reflects the mock-processed state; the typed key is never rendered
    expect(screen.queryByText(TEST_KEY)).not.toBeInTheDocument();
  });

  it("save failure remains failure and does not fake success", async () => {
    const client = createMockClient();
    vi.spyOn(client, "updateLlmSettings").mockRejectedValue(new Error("save boom"));

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "changed-model-id" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText(/Save failed: save boom/)).toBeInTheDocument();
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
    // The typed key input still clears safely, and the sanitized view is unchanged
    expect((screen.getByLabelText("API key (configured)") as HTMLInputElement).value).toBe("");
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("fallback key behaves equivalently: omitted when blank, sent when typed", async () => {
    const fetchMock = vi.fn().mockImplementation(() => jsonResponse(fixtureSettings));
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    // Omitted when blank/absent
    await client.updateLlmSettings({ model: "m1" });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ model: "m1" });

    // Sent when typed
    await client.updateLlmSettings({ fallback_api_key: TEST_KEY });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ fallback_api_key: TEST_KEY });
  });
});

describe("Settings detect models (POST /api/settings/llm/detect)", () => {
  it("sends the exact endpoint with no body and renders model IDs and capabilities", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      jsonResponse({
        models: [
          { id: "rest-model-a", display_name: "Rest Model A", supports_tools: true, supports_structured_output: false, context_window: 8192 },
          { id: "rest-model-b", display_name: "Rest Model B", supports_tools: null, supports_structured_output: null, context_window: null },
        ],
      } satisfies DetectModelsResponse)
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const response = await client.detectLlmModels();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/settings/llm/detect");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(response.models).toHaveLength(2);
  });

  it("renders detected models with Unknown for null capabilities, and selecting populates the local Model ID", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    // Form is clean initially â†’ Detect enabled
    fireEvent.click(screen.getByRole("button", { name: "Detect models" }));

    expect(await screen.findByText("demo-served-model-id")).toBeInTheDocument();
    expect(screen.getByText("demo-fast-model-id")).toBeInTheDocument();
    expect(screen.getByText("demo-capability-unknown-model-id")).toBeInTheDocument();
    expect(screen.getByText("Tools: Unknown")).toBeInTheDocument();
    expect(screen.getByText("Structured: Unknown")).toBeInTheDocument();
    expect(screen.getByText("Tools: Yes")).toBeInTheDocument();
    expect(screen.getByText("Tools: No")).toBeInTheDocument();

    // Selecting a model populates the local field only; user still saves explicitly
    fireEvent.click(screen.getAllByRole("button", { name: "Use" })[1]);
    expect(screen.getByLabelText("Model ID")).toHaveValue("demo-fast-model-id");
  });

  it("disables Detect Models AND Test Connection while the form is dirty and explains why", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    // Clean form → both enabled
    expect(screen.getByRole("button", { name: "Detect models" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();

    // Edit → both disabled, explanatory saved-settings message visible
    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "unsaved-model" } });
    expect(screen.getByRole("button", { name: "Detect models" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeDisabled();
    expect(screen.getByText("Detect/test use saved settings — save first.")).toBeInTheDocument();

    // Successful save → form clean again → both re-enabled
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText("Saved");
    expect(screen.getByRole("button", { name: "Detect models" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Test connection" })).toBeEnabled();
    expect(screen.queryByText("Detect/test use saved settings — save first.")).not.toBeInTheDocument();
  });

  it("detect failure remains failure", async () => {
    const client = createMockClient({ mode: "error" });
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);

    // In error mode even the initial read fails; detection can never fake success
    expect(await screen.findByText("Settings unavailable")).toBeInTheDocument();
    expect(screen.queryByText("demo-served-model-id")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });
});

describe("Settings test connection (POST /api/settings/llm/test)", () => {
  it("sends the exact endpoint with no body and returns the typed response", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      jsonResponse({
        health: { status: "ready", provider: "openai_compatible", model: "demo-served-model-id" },
        latency_ms: 42,
        model: "demo-served-model-id",
      } satisfies LlmTestConnectionResponse)
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createRestClient();
    const response = await client.testLlmConnection();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/settings/llm/test");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
    expect(response.health.status).toBe("ready");
    expect(response.latency_ms).toBe(42);
  });

  it("renders ready state with provider, model and latency", async () => {
    // Fresh client: earlier tests in this file mutate the shared singleton's
    // configured model, and the mock reports the currently configured model.
    const client = createMockClient();
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText(/Last test/)).toBeInTheDocument();
    expect(screen.getByText("42 ms")).toBeInTheDocument();
    expect(screen.getAllByText("demo-served-model-id").length).toBeGreaterThan(0);
    expect(screen.getByText(/Mock transport: no real endpoint contacted/)).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("does not fabricate latency when the response omits it", async () => {
    const client = createMockClient();
    // Degraded health without latency
    vi.spyOn(client, "testLlmConnection").mockResolvedValue({
      health: { status: "degraded", detail: "high latency upstream" },
    } as LlmTestConnectionResponse);

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("Latency not reported.")).toBeInTheDocument();
    expect(screen.queryByText(/ms/)).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("renders unavailable status truthfully from the response", async () => {
    const client = createMockClient();
    vi.spyOn(client, "testLlmConnection").mockResolvedValue({
      health: { status: "unavailable", detail: "connection refused" },
    } as LlmTestConnectionResponse);

    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("connection-test failure remains failure", async () => {
    const client = createMockClient();
    vi.spyOn(client, "testLlmConnection").mockRejectedValue(new Error("connection refused"));
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText(/Connection test failed: connection refused/)).toBeInTheDocument();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });
});

describe("Settings cloud status", () => {
  it("renders cloud flags from the response as read-only status with no toggles", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    expect(screen.getByText(/Cloud inference:/)).toBeInTheDocument();
    // Both cloud flags render as read-only status
    expect(screen.getAllByText("Disabled", { exact: false }).length).toBe(2);
    expect(screen.getByText(/backend-managed in the current MVP/)).toBeInTheDocument();
    // No editable cloud controls exist — the update contract does not expose them
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
});

describe("Settings mock transport", () => {
  it("updates presence flags without retaining or returning secret strings", async () => {
    const client = createMockClient();

    // Canonical initial state
    const initial = await client.getLlmSettings();
    expect(initial).toMatchObject({
      provider: "openai_compatible",
      api_key_present: true,
      fallback_api_key_present: false,
      configured: true,
    });

    // Update with a new key: presence flips, secret is never returned
    const afterKey = await client.updateLlmSettings({ api_key: TEST_KEY, model: "demo-served-model-id" });
    expect(afterKey.api_key_present).toBe(true);
    expect(JSON.stringify(afterKey)).not.toContain(TEST_KEY);

    // Omitted fields preserve state; explicit null removes the key
    const afterNull = await client.updateLlmSettings({ api_key: null });
    expect(afterNull.api_key_present).toBe(false);
    expect(afterNull.model).toBe("demo-served-model-id");

    // Fallback key removal
    const afterFallback = await client.updateLlmSettings({
      fallback_base_url: "http://backup.tailnet.example:8000/v1",
      fallback_model: "demo-fast-model-id",
      fallback_api_key: TEST_KEY,
    });
    expect(afterFallback.fallback_api_key_present).toBe(true);
    expect(JSON.stringify(afterFallback)).not.toContain(TEST_KEY);
    const afterFallbackClear = await client.updateLlmSettings({ fallback_api_key: null });
    expect(afterFallbackClear.fallback_api_key_present).toBe(false);
  });

  it("detect and test are deterministic and clearly synthetic", async () => {
    const client = createMockClient();

    const detected = await client.detectLlmModels();
    expect(detected.models.map((m) => m.id)).toEqual([
      "demo-served-model-id",
      "demo-fast-model-id",
      "demo-capability-unknown-model-id",
    ]);

    const test = await client.testLlmConnection();
    expect(test.health.status).toBe("ready");
    expect(test.health.model).toBe("demo-served-model-id");
    expect(test.latency_ms).toBe(42);
    expect(test.health.detail).toContain("no real endpoint contacted");
  });

  it("mock error mode keeps failures as failures for settings operations", async () => {
    const failing = createMockClient({ mode: "error" });
    await expect(failing.getLlmSettings()).rejects.toThrow("Simulated transport failure");
    await expect(failing.updateLlmSettings({ model: "x" })).rejects.toThrow("Simulated transport failure");
    await expect(failing.testLlmConnection()).rejects.toThrow("connection refused");
    await expect(failing.detectLlmModels()).rejects.toThrow("connection refused");
  });

  it("two mock clients do not share settings state", async () => {
    const clientA = createMockClient();
    const clientB = createMockClient();

    await clientA.updateLlmSettings({ model: "a-only-model" });
    const a = await clientA.getLlmSettings();
    const b = await clientB.getLlmSettings();
    expect(a.model).toBe("a-only-model");
    expect(b.model).toBe("demo-served-model-id");
  });
});


describe("Settings state remediation (reviewed slice hardening)", () => {
  it("clearing a configured Base URL saves base_url: null, never an empty string", async () => {
    const client = createMockClient();
    const updateSpy = vi.spyOn(client, "updateLlmSettings");
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    // Initial configured Base URL exists
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://demo-host.tailnet.example:8321/v1");

    // User clears the field and saves
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();

    // PUT request carries base_url: null — never ""
    expect(updateSpy).toHaveBeenCalledTimes(1);
    const request = updateSpy.mock.calls[0][0] as LlmSettingsUpdateRequest;
    expect(request.base_url).toBeNull();
    expect(request.base_url).not.toBe("");

    // Sanitized mock state reflects the null URL truthfully
    const settings = await client.getLlmSettings();
    expect(settings.base_url).toBeNull();
    expect(settings.configured).toBe(false);
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("Save settings is disabled while the form is clean and enabled once dirty", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "changed-model" } });
    expect(screen.getByRole("button", { name: "Save settings" })).toBeEnabled();
  });

  it("detected model list from a previous configuration disappears after saving a change", async () => {
    const client = createMockClient();
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    // Detect models against the saved configuration
    fireEvent.click(screen.getByRole("button", { name: "Detect models" }));
    expect(await screen.findByText("demo-served-model-id")).toBeInTheDocument();

    // Change the model and save — the stale list must not survive
    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "brand-new-model" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("demo-served-model-id")).not.toBeInTheDocument();
    expect(screen.queryByText("demo-fast-model-id")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("previous connection-test result disappears after saving a changed configuration", async () => {
    const client = createMockClient();
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    // Test connection against the saved configuration
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("42 ms")).toBeInTheDocument();

    // Change the model and save — stale health/latency must not survive
    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "brand-new-model" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
    expect(screen.queryByText("Ready")).not.toBeInTheDocument();
    expect(screen.queryByText("42 ms")).not.toBeInTheDocument();
    expect(screen.queryByText(/Last test/)).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("Saved indicator disappears once the user edits again after a successful save", async () => {
    render(<Settings />);
    await screen.findByText("openai_compatible");

    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "saved-model" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Saved")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Model ID"), { target: { value: "edited-again" } });
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("renders the top-level tested model when health.model is absent (A)", async () => {
    const client = createMockClient();
    vi.spyOn(client, "testLlmConnection").mockResolvedValue({
      health: { status: "ready", provider: "openai_compatible" },
      model: "actual-server-model-id",
      latency_ms: 40,
    });
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("actual-server-model-id")).toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("invents no model when neither health.model nor response.model is present (B)", async () => {
    const client = createMockClient();
    vi.spyOn(client, "testLlmConnection").mockResolvedValue({
      health: { status: "unavailable", detail: "connection refused" },
      latency_ms: null,
    });
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    // No fabricated model ID anywhere in the rendered result
    expect(screen.queryByText("actual-server-model-id")).not.toBeInTheDocument();
    expect(screen.queryByText("demo-served-model-id")).not.toBeInTheDocument();
    vi.doUnmock("../api/client");
    vi.resetModules();
  });

  it("renders a duplicated model id only once when both fields agree (C)", async () => {
    const client = createMockClient();
    vi.spyOn(client, "testLlmConnection").mockResolvedValue({
      health: { status: "ready", provider: "openai_compatible", model: "same-model-id" },
      model: "same-model-id",
      latency_ms: 40,
    });
    vi.resetModules();
    vi.doMock("../api/client", async (importOriginal) => {
      const actual = await importOriginal<typeof import("../api/client")>();
      return { ...actual, getEvaClient: () => client };
    });
    const { default: SettingsFresh } = await import("../pages/Settings");
    render(<SettingsFresh />);
    await screen.findByText("openai_compatible");

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getAllByText("same-model-id")).toHaveLength(1);
    vi.doUnmock("../api/client");
    vi.resetModules();
  });
});
