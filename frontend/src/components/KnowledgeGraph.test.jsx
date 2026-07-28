import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import KnowledgeGraph, {
  buildModel,
  colorOf,
  edgeId,
  heightFor,
  isDashed,
  radiusOf,
} from "./KnowledgeGraph";
import { CAREER_PATH_COLOR, categoryColor } from "../categories";

// The graph's pure model/derivation helpers. The <KnowledgeGraph> render path is
// a d3-force simulation over live DOM measurement — untestable under jsdom
// without a canvas — but everything that decides *what connects to what* and
// *what color/size a mark is* lives in these functions, so they carry the rules
// worth pinning. buildModel in particular is what the highlight interaction and
// the detail panel read; a wrong edge here is a wrong story on the money screen.

describe("buildModel", () => {
  // A small graph shaped like the demo chain: a skill hub joining a cert, a
  // project, and a career path, plus one similarity edge.
  function sampleData() {
    return {
      nodes: [
        { id: "doc_cert", type: "document", category: "Certifications", label: "Python Cert" },
        { id: "doc_proj", type: "document", category: "Projects", label: "ML Pipeline" },
        { id: "skill_py", type: "skill", label: "Python" },
        { id: "career_ml", type: "career_path", label: "AI/ML Engineer", match_score: 0.87 },
      ],
      edges: [
        { source: "doc_cert", target: "skill_py", relation_type: "certifies_skill" },
        { source: "skill_py", target: "doc_proj", relation_type: "skill_used_in" },
        { source: "doc_proj", target: "career_ml", relation_type: "leads_to" },
        { source: "doc_cert", target: "doc_proj", relation_type: "similar_to" },
      ],
    };
  }

  it("copies nodes and edges rather than mutating the input", () => {
    // d3 mutates the node/link arrays it owns (x/y, and it resolves link
    // endpoints from ids to node objects). buildModel must hand it copies so the
    // raw {nodes, edges} from the API stays intact for the next rebuild.
    const data = sampleData();
    const model = buildModel(data);
    expect(model.nodes[0]).not.toBe(data.nodes[0]);
    expect(model.nodes[0]).toEqual(data.nodes[0]);
    expect(model.links[0]).not.toBe(data.edges[0]);
    expect(model.links).toHaveLength(data.edges.length);
  });

  it("indexes every node by id", () => {
    const model = buildModel(sampleData());
    expect(model.byId.get("skill_py").label).toBe("Python");
    expect(model.byId.size).toBe(4);
  });

  it("makes a node its own neighbour so the highlight includes the selection", () => {
    // The highlight set is seeded with the node itself; selecting a node dims
    // everything except its neighbours, and the node must not dim itself.
    const model = buildModel(sampleData());
    expect(model.neighbors.get("skill_py").has("skill_py")).toBe(true);
  });

  it("links neighbours symmetrically across each edge", () => {
    const model = buildModel(sampleData());
    // skill_py touches the cert and the project.
    expect(model.neighbors.get("skill_py")).toEqual(
      new Set(["skill_py", "doc_cert", "doc_proj"])
    );
    // and each of those names skill_py back.
    expect(model.neighbors.get("doc_cert").has("skill_py")).toBe(true);
    expect(model.neighbors.get("doc_proj").has("skill_py")).toBe(true);
  });

  it("dedups neighbours when two edges join the same pair", () => {
    const data = {
      nodes: [
        { id: "a", type: "document", category: "Projects" },
        { id: "b", type: "skill" },
      ],
      // Two distinct relations between the same pair — a real case: a doc can be
      // both similar_to and skill_used_in with the same neighbour.
      edges: [
        { source: "a", target: "b", relation_type: "skill_used_in" },
        { source: "a", target: "b", relation_type: "similar_to" },
      ],
    };
    const model = buildModel(data);
    // The neighbour set counts b once (Set), so highlight opacity is stable...
    expect(model.neighbors.get("a")).toEqual(new Set(["a", "b"]));
    // ...but connections keep both edges, since the panel lists each relation.
    expect(model.connections.get("a")).toHaveLength(2);
  });

  it("records connections bidirectionally with the edge relation", () => {
    const model = buildModel(sampleData());
    const fromCert = model.connections.get("doc_cert");
    expect(fromCert).toContainEqual({
      node: model.byId.get("skill_py"),
      relation: "certifies_skill",
    });
    // The reverse direction is present too, so the panel works from either end.
    const fromSkill = model.connections.get("skill_py");
    expect(fromSkill.map((c) => c.node.id)).toContain("doc_cert");
  });

  it("drops an edge whose endpoint is missing instead of crashing", () => {
    // A career-path edge can point at a node the graph query didn't return; the
    // guard must skip it, not throw on neighbors.get(undefined). (Mutation target:
    // remove the byId.has check and this test throws.)
    const data = {
      nodes: [{ id: "a", type: "document", category: "Projects" }],
      edges: [
        { source: "a", target: "ghost", relation_type: "leads_to" },
        { source: "missing", target: "a", relation_type: "similar_to" },
      ],
    };
    let model;
    expect(() => {
      model = buildModel(data);
    }).not.toThrow();
    expect(model.neighbors.get("a")).toEqual(new Set(["a"])); // no ghost
    expect(model.connections.get("a")).toHaveLength(0);
  });

  it("handles an empty graph", () => {
    const model = buildModel({ nodes: [], edges: [] });
    expect(model.nodes).toHaveLength(0);
    expect(model.byId.size).toBe(0);
    expect(model.neighbors.size).toBe(0);
  });
});

describe("radiusOf", () => {
  it("sizes each node kind, career paths largest and skills smallest", () => {
    // The size difference is half of Career Path's composite encoding and makes
    // document hubs read as the primary objects (categories.js / plan.md §6).
    expect(radiusOf({ type: "career_path" })).toBeGreaterThan(radiusOf({ type: "document" }));
    expect(radiusOf({ type: "document" })).toBeGreaterThan(radiusOf({ type: "skill" }));
  });

  it("falls back to a default radius for an unknown kind", () => {
    expect(radiusOf({ type: "mystery" })).toBe(9);
  });
});

describe("colorOf", () => {
  it("gives a document its category hue", () => {
    expect(colorOf({ type: "document", category: "Projects" })).toBe(categoryColor("Projects"));
  });

  it("gives every skill the shared Skills hue regardless of category field", () => {
    expect(colorOf({ type: "skill" })).toBe(categoryColor("Skills"));
  });

  it("gives a career path the reserved achromatic slate, never a category hue", () => {
    // Career Path deliberately has no categorical hue (categories.js); it must
    // read as a different *kind* of thing, not a seventh category.
    expect(colorOf({ type: "career_path" })).toBe(CAREER_PATH_COLOR);
  });
});

describe("isDashed", () => {
  it("dashes only the non-obvious similarity link", () => {
    // Layer B similar_to edges are the "discovery" connection and render dashed;
    // the entity/career chain edges are solid.
    expect(isDashed("similar_to")).toBe(true);
    expect(isDashed("certifies_skill")).toBe(false);
    expect(isDashed("skill_used_in")).toBe(false);
    expect(isDashed("leads_to")).toBe(false);
  });
});

describe("edgeId", () => {
  it("keys an edge by its endpoints whether they are ids or resolved nodes", () => {
    // d3 rewrites link.source/target from a string id to the node object in
    // place; edgeId must yield the same stable React key before and after.
    expect(edgeId({ source: "a", target: "b" })).toBe("a->b");
    expect(edgeId({ source: { id: "a" }, target: { id: "b" } })).toBe("a->b");
  });
});

describe("heightFor", () => {
  it("shortens the surface on a phone-width container", () => {
    // 560px of graph is most of a phone viewport before the legend and the
    // interaction hint below it are even reached.
    expect(heightFor(390)).toBeLessThan(heightFor(1200));
  });

  it("keeps the full height on a desktop container", () => {
    expect(heightFor(1200)).toBe(560);
    expect(heightFor(640)).toBe(560); // the breakpoint itself is not narrow
  });

  it("treats an unmeasured width as desktop, never as zero-height", () => {
    // clientWidth reads 0 for a hidden container. Returning a narrow height for
    // that would be acting on a measurement that never happened.
    expect(heightFor(0)).toBe(560);
  });
});

describe("<KnowledgeGraph /> sizing", () => {
  // The regression these guard: the graph surface only exists *after* the fetch
  // resolves, because the component early-returns a skeleton while loading. The
  // width measurement used to run once on mount against a ref that was still
  // null, never re-run, and leave the SVG at its 720px default — clipped by the
  // container's overflow-hidden on any phone. Only ~a quarter of the graph was
  // reachable, and only on narrow screens, so every desktop check passed.
  function stubGraphFetch() {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        nodes: [
          { id: "doc_a", type: "document", category: "Projects", label: "ML Pipeline" },
          { id: "skill_py", type: "skill", label: "Python" },
        ],
        edges: [{ source: "doc_a", target: "skill_py", relation_type: "skill_used_in" }],
      }),
    });
  }

  it("never gives the surface a fixed pixel width", async () => {
    stubGraphFetch();
    const { container } = render(<KnowledgeGraph />);

    await waitFor(() => expect(container.querySelector("svg[viewBox]")).toBeTruthy());
    // The last svg is the graph surface (the legend renders tiny line swatches).
    const surface = container.querySelector("svg.touch-none");

    expect(surface).toBeTruthy();
    expect(surface.getAttribute("width")).toBe("100%");
  });

  it("measures the container once it exists, rather than only on mount", async () => {
    // The container mounts *after* the fetch resolves. If the measurement does
    // not re-run at that point, the viewBox keeps the default width and the
    // simulation lays out in a field wider than the screen.
    stubGraphFetch();
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(390);

    const { container } = render(<KnowledgeGraph />);

    await waitFor(() => expect(container.querySelector("svg.touch-none")).toBeTruthy());
    await waitFor(() => {
      const viewBox = container.querySelector("svg.touch-none").getAttribute("viewBox");
      expect(viewBox).toBe(`0 0 390 ${heightFor(390)}`);
    });
  });
});
