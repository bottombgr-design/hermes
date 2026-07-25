// @ts-nocheck
// Plain IIFE source; the dashboard host supplies the runtime SDK.
(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const React = SDK.React;
  const h = React.createElement;
  const { useCallback, useEffect, useMemo, useState } = SDK.hooks;
  const C = SDK.components;
  const cn = SDK.utils.cn;
  const API = "/api/plugins/control-centre/snapshot";
  const NOT_CONFIGURED = "Not configured";

  function spread(type, props, children) {
    return h.apply(null, [type, props].concat(children));
  }

  function present(value) {
    return value === null || value === undefined || value === "" ? NOT_CONFIGURED : String(value);
  }

  function titleCase(value) {
    return present(value).replace(/(^|[-_\s])([a-z])/g, function (_match, space, letter) {
      return (space ? " " : "") + letter.toUpperCase();
    });
  }

  function safeHref(value, fallback) {
    return typeof value === "string" && value.startsWith("/") ? value : fallback;
  }

  function formatTime(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return NOT_CONFIGURED;
    return new Date(value * 1000).toLocaleString();
  }

  function sourceMeta(source) {
    const ref = source || {};
    return h(
      "div",
      { className: "hermes-control-source", "data-freshness": present(ref.freshness) },
      h("span", { className: cn("hermes-control-dot", "is-" + present(ref.freshness).toLowerCase()) }),
      h("span", null, present(ref.source) + " / " + present(ref.source_id)),
      h("span", null, titleCase(ref.freshness)),
      h("time", { dateTime: typeof ref.observed_at === "number" ? new Date(ref.observed_at * 1000).toISOString() : undefined }, formatTime(ref.observed_at))
    );
  }

  function detail(label, value, className) {
    return h(
      "div",
      { className: cn("hermes-control-detail", className) },
      h("dt", null, label),
      h("dd", null, present(value))
    );
  }

  function deepLink(href, label, fallback) {
    const target = safeHref(href, fallback);
    return target
      ? h("a", { href: target, className: "hermes-control-link" }, label)
      : h("span", { className: "hermes-control-muted" }, NOT_CONFIGURED);
  }

  function sectionHeader(title, count, detailText) {
    return h(
      "div",
      { className: "hermes-control-section-head" },
      h("div", null, h("h2", null, title), detailText ? h("p", null, detailText) : null),
      typeof count === "number" ? h("span", { className: "hermes-control-count" }, String(count)) : null
    );
  }

  function attentionCard(item) {
    return h(
      C.Card,
      { key: item.source.source_id, className: cn("hermes-control-card", "is-" + item.severity) },
      h(
        C.CardContent,
        { className: "hermes-control-card-content" },
        h("div", { className: "hermes-control-card-head" },
          h("strong", null, present(item.title)),
          h("span", { className: "hermes-control-severity" }, titleCase(item.severity))
        ),
        h("p", null, present(item.detail)),
        h("div", { className: "hermes-control-tags" },
          item.product_id ? h("span", null, "Product " + item.product_id) : null,
          item.agent_id ? h("span", null, "Agent " + item.agent_id) : null
        ),
        sourceMeta(item.source),
        deepLink(item.source && item.source.href, "Open source", "/kanban")
      )
    );
  }

  function agentCard(agent, detailed) {
    const diagnostics = Array.isArray(agent.diagnostics) ? agent.diagnostics : [];
    return h(
      C.Card,
      { key: agent.profile, className: "hermes-control-card hermes-control-agent" },
      h(
        C.CardContent,
        { className: "hermes-control-card-content" },
        h("div", { className: "hermes-control-card-head" },
          h("strong", null, titleCase(agent.profile)),
          h("span", { className: cn("hermes-control-state", "is-" + present(agent.state).toLowerCase()) }, titleCase(agent.state))
        ),
        h("p", { className: "hermes-control-role" }, present(agent.role)),
        h("dl", { className: "hermes-control-details" },
          detail("Current task", agent.task_id),
          detail("Run", agent.run_id),
          detail("Last heartbeat", formatTime(agent.last_heartbeat)),
          detail("Lease expires", formatTime(agent.lease_expires_at)),
          detailed ? detail("Model", agent.model) : null,
          detailed ? detail("Provider", agent.provider) : null,
          detailed ? detail("Skills", agent.skill_count) : null,
          detailed ? detail("Workspace", agent.workspace) : null
        ),
        diagnostics.length
          ? spread("ul", { className: "hermes-control-diagnostics" }, diagnostics.map(function (message, index) {
              return h("li", { key: index }, present(message));
            }))
          : h("p", { className: "hermes-control-muted" }, "No diagnostics reported"),
        sourceMeta(agent.source),
        h("div", { className: "hermes-control-actions" },
          deepLink(agent.chat_href, "Open Chat", "/chat?profile=" + encodeURIComponent(agent.profile)),
          deepLink("/skills", "Skills", "/skills"),
          deepLink(agent.source && agent.source.href, "Task", "/kanban")
        )
      )
    );
  }

  function productCard(product, detailed) {
    const repositories = Array.isArray(product.repositories) ? product.repositories : [];
    const environments = Array.isArray(product.environments) ? product.environments : [];
    return h(
      C.Card,
      { key: product.product_id, className: "hermes-control-card hermes-control-product" },
      h(
        C.CardContent,
        { className: "hermes-control-card-content" },
        h("div", { className: "hermes-control-card-head" },
          h("strong", null, present(product.name)),
          h("span", { className: cn("hermes-control-state", "is-" + present(product.configuration_state)) }, titleCase(product.configuration_state))
        ),
        h("p", { className: "hermes-control-id" }, present(product.product_id)),
        detailed
          ? h("div", { className: "hermes-control-inspector" },
              h("h3", null, "Repositories"),
              repositories.length
                ? spread("ul", { className: "hermes-control-list" }, repositories.map(function (repository) {
                    return h("li", { key: repository.id },
                      h("strong", null, present(repository.id)),
                      h("span", null, "Remote: " + present(repository.remote)),
                      h("span", null, "Default branch: " + present(repository.default_branch))
                    );
                  }))
                : h("p", { className: "hermes-control-muted" }, NOT_CONFIGURED),
              h("h3", null, "Environments"),
              environments.length
                ? spread("ul", { className: "hermes-control-list" }, environments.map(function (environment, index) {
                    return h("li", {
                      key: present(environment.name) + index,
                      className: environment.production ? "hermes-control-production" : "hermes-control-environment"
                    },
                    h("strong", null, present(environment.name)),
                    h("span", null, environment.production ? "Production environment" : "Non-production environment")
                    );
                  }))
                : h("p", { className: "hermes-control-muted" }, NOT_CONFIGURED),
              h("p", { className: "hermes-control-muted" }, product.recovery || "No configuration recovery needed")
            )
          : null,
        sourceMeta(product.source),
        h("div", { className: "hermes-control-actions" },
          deepLink(product.kanban_href, "Kanban", "/kanban"),
          deepLink(product.chat_href, "Chat", "/chat?product=" + encodeURIComponent(product.product_id)),
          deepLink(product.sessions_href, "Sessions", "/sessions?product=" + encodeURIComponent(product.product_id))
        )
      )
    );
  }

  function buildCard(build) {
    const verdicts = build.layer_verdicts && typeof build.layer_verdicts === "object" ? build.layer_verdicts : {};
    return h(
      C.Card,
      { key: build.release_id, className: "hermes-control-card hermes-control-build" },
      h(C.CardContent, { className: "hermes-control-card-content" },
        h("div", { className: "hermes-control-card-head" },
          h("strong", null, present(build.release_id)),
          h("span", { className: cn("hermes-control-verdict", "is-" + present(build.state).toLowerCase()) }, present(build.state))
        ),
        h("dl", { className: "hermes-control-details" },
          detail("Product", build.product_id),
          detail("Manifest", build.manifest_hash),
          detail("Review", build.review_state),
          detail("Deployment", build.deployment_outcome)
        ),
        spread("div", { className: "hermes-control-verdicts" }, Object.keys(verdicts).sort().map(function (layer) {
          return h("span", { key: layer, className: "hermes-control-verdict-row" }, layer + ": " + present(verdicts[layer]));
        })),
        sourceMeta(build.source)
      )
    );
  }

  function errorCard(error) {
    return h(
      "div",
      { key: error.source.source_id, className: cn("hermes-control-source-error", "is-" + error.severity), role: "status" },
      h("strong", null, present(error.message)),
      h("p", null, present(error.recovery)),
      sourceMeta(error.source)
    );
  }

  function Filters({ data, filters, setFilters }) {
    const products = data.products || [];
    const agents = data.agents || [];
    function filterControl(label, key, values) {
      function update(value) {
        setFilters(function (current) { return Object.assign({}, current, { [key]: value }); });
      }
      return h("label", { className: "hermes-control-filter" },
        h("span", null, label),
        h("select", {
          value: filters[key],
          onChange: function (event) { update(event.target.value); },
          onValueChange: update,
          "aria-label": label
        }, ...values.map(function (option) {
          return h("option", { key: option.value, value: option.value }, option.label);
        }))
      );
    }
    return h("div", { className: "hermes-control-filters", "aria-label": "Control Centre filters" },
      filterControl("Product", "product", [{ value: "all", label: "All products" }].concat(products.map(function (product) {
        return { value: product.product_id, label: present(product.name) };
      }))),
      filterControl("Agent", "agent", [{ value: "all", label: "All agents" }].concat(agents.map(function (agent) {
        return { value: agent.profile, label: titleCase(agent.profile) };
      }))),
      filterControl("Severity", "severity", ["all", "critical", "error", "warning", "info"].map(function (value) {
        return { value: value, label: titleCase(value) };
      })),
      filterControl("Freshness", "freshness", ["all", "live", "cached", "stale", "unavailable"].map(function (value) {
        return { value: value, label: titleCase(value) };
      }))
    );
  }

  function Navigation({ view, setView }) {
    return spread("nav", { className: "hermes-control-nav", "aria-label": "Control Centre sections" },
      ["Overview", "Attention", "Builds", "Agents", "Products", "System"].map(function (label) {
        const value = label.toLowerCase();
        return h("button", {
          key: value,
          type: "button",
          className: value === view ? "is-active" : "",
          "aria-current": value === view ? "page" : undefined,
          onClick: function () { setView(value); }
        }, label);
      })
    );
  }

  function ControlCentrePage() {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [view, setView] = useState("overview");
    const [filters, setFilters] = useState({ product: "all", agent: "all", severity: "all", freshness: "all" });

    const load = useCallback(function () {
      setError(null);
      return SDK.fetchJSON(API)
        .then(function (snapshot) { setData(snapshot); })
        .catch(function (reason) { setError(reason instanceof Error ? reason.message : String(reason)); });
    }, []);

    useEffect(function () { void load(); }, [load]);

    const filtered = useMemo(function () {
      if (!data) return { attention: [], agents: [], products: [], builds: [] };
      function fresh(item) {
        return filters.freshness === "all" || (item.source && item.source.freshness === filters.freshness);
      }
      function matchesProduct(item) {
        const productId = item && item.product_id;
        return filters.product === "all" || !productId || productId === "unassigned" || productId === filters.product;
      }
      return {
        attention: (data.attention || []).filter(function (item) {
          return fresh(item) && matchesProduct(item) &&
            (filters.agent === "all" || item.agent_id === filters.agent) &&
            (filters.severity === "all" || item.severity === filters.severity);
        }),
        agents: (data.agents || []).filter(function (item) {
          return fresh(item) && matchesProduct(item) &&
            (filters.agent === "all" || item.profile === filters.agent);
        }),
        products: (data.products || []).filter(function (item) {
          return fresh(item) && (filters.product === "all" || item.product_id === filters.product);
        }),
        builds: (data.builds || []).filter(function (item) {
          return fresh(item) && matchesProduct(item);
        })
      };
    }, [data, filters]);

    if (error) {
      return h("main", { className: "hermes-control hermes-control-failure", role: "alert" },
        h("span", { className: "hermes-control-kicker" }, "Source unavailable"),
        h("h1", null, "Control Centre unavailable"),
        h("p", null, present(error)),
        h("button", { type: "button", onClick: load }, "Retry")
      );
    }

    if (!data) {
      return h("main", { className: "hermes-control hermes-control-loading", "aria-busy": "true" },
        h("span", { className: "hermes-control-kicker" }, "Read-only operator surface"),
        h("h1", null, "Loading Control Centre"),
        h("p", null, "Gathering freshness-stamped records from authoritative Hermes sources."),
        h("div", { className: "hermes-control-loading-bar", "aria-hidden": "true" })
      );
    }

    const empty = !(data.products || []).length;
    const show = function (name) { return view === "overview" || view === name; };
    return h("main", { className: "hermes-control" },
      h("header", { className: "hermes-control-hero" },
        h("div", null,
          h("span", { className: "hermes-control-kicker" }, "Monitor + navigate / read-only"),
          h("h1", null, "Control Centre"),
          h("p", null, "Attention-first visibility across products, agents, factory evidence, and source health.")
        ),
        h("div", { className: "hermes-control-generated" },
          h("span", null, "Snapshot generated"),
          h("strong", null, formatTime(data.generated_at)),
          h("button", { type: "button", onClick: load }, "Refresh")
        )
      ),
      h(Navigation, { view: view, setView: setView }),
      h(Filters, { data: data, filters: filters, setFilters: setFilters }),
      (data.source_errors || []).length
        ? spread("section", { className: "hermes-control-errors", "aria-label": "Source errors" }, (data.source_errors || []).map(errorCard))
        : null,
      empty
        ? h("section", { className: "hermes-control-empty" },
            h("h2", null, "No products configured"),
            h("p", null, (data.setup_guidance || []).join(" ") || "Create products.yaml to configure products."),
            deepLink("/plugins", "Open Plugins", "/plugins")
          )
        : null,
      show("attention") ? h("section", { className: "hermes-control-section" },
        sectionHeader("Attention", filtered.attention.length, "Blocked, stale, degraded, and review-bound work."),
        filtered.attention.length
          ? spread("div", { className: "hermes-control-grid" }, filtered.attention.map(attentionCard))
          : h("p", { className: "hermes-control-empty-row" }, "No attention items match the active filters.")
      ) : null,
      show("builds") ? h("section", { className: "hermes-control-section" },
        sectionHeader("Active Builds", filtered.builds.length, "Recorded Agent Factory evidence; verdicts are not reinterpreted."),
        filtered.builds.length
          ? spread("div", { className: "hermes-control-grid" }, filtered.builds.map(buildCard))
          : h("p", { className: "hermes-control-empty-row" }, "No build evidence is configured.")
      ) : null,
      show("agents") ? h("section", { className: "hermes-control-section" },
        sectionHeader("Agent Fleet", filtered.agents.length, "Profile, task, run, heartbeat, lease, and diagnostics."),
        filtered.agents.length
          ? spread("div", { className: "hermes-control-grid" }, filtered.agents.map(function (agent) { return agentCard(agent, view === "agents"); }))
          : h("p", { className: "hermes-control-empty-row" }, "No agents match the active filters.")
      ) : null,
      show("products") ? h("section", { className: "hermes-control-section" },
        sectionHeader("Products", filtered.products.length, "Configuration references and links into existing Hermes surfaces."),
        filtered.products.length
          ? spread("div", { className: "hermes-control-grid" }, filtered.products.map(function (product) { return productCard(product, view === "products"); }))
          : h("p", { className: "hermes-control-empty-row" }, "No products match the active filters.")
      ) : null,
      show("system") ? h("section", { className: "hermes-control-section" },
        sectionHeader("System", undefined, "Source freshness and canonical operational surfaces."),
        h("div", { className: "hermes-control-system" },
          detail("Schema", data.schema_version),
          detail("Stale after", present(data.stale_after_seconds) + " seconds"),
          detail("Source errors", (data.source_errors || []).length),
          h("div", { className: "hermes-control-actions" },
            deepLink("/cron", "Cron", "/cron"), deepLink("/logs", "Logs", "/logs"),
            deepLink("/system", "System", "/system"), deepLink("/sessions", "Sessions", "/sessions")
          )
        )
      ) : null
    );
  }

  registry.register("control-centre", ControlCentrePage);
})();
