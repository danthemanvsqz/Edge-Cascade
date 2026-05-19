import { describe, it, expect } from "vitest";
import { h, Fragment, raw } from "../src/vnode.js";
import type { Props } from "../src/vnode.js";
import {
  renderToString,
  renderToStream,
  escapeText,
  escapeAttr,
} from "../src/render.js";

async function collect(node: unknown): Promise<string> {
  let out = "";
  for await (const chunk of renderToStream(node)) out += chunk;
  return out;
}

describe("escaping (load-bearing — XSS)", () => {
  it("escapeText handles & < > and orders & first", () => {
    expect(escapeText("a & b < c > d")).toBe("a &amp; b &lt; c &gt; d");
    expect(escapeText("&lt;")).toBe("&amp;lt;");
  });

  it("escapeAttr also escapes double quotes", () => {
    expect(escapeAttr(`x" onload="alert(1)`)).toBe(
      "x&quot; onload=&quot;alert(1)",
    );
  });

  it("text children are escaped by default", () => {
    expect(renderToString(h("div", null, '<script>alert("xss")</script>'))).toBe(
      "<div>&lt;script&gt;alert(\"xss\")&lt;/script&gt;</div>",
    );
  });

  it("attribute values are escaped (cannot break out of the quote)", () => {
    expect(
      renderToString(h("a", { title: 'evil" onmouseover="x' })),
    ).toBe('<a title="evil&quot; onmouseover=&quot;x"></a>');
  });

  it("raw() is the only unescaped path", () => {
    expect(renderToString(raw("<i>ok</i>"))).toBe("<i>ok</i>");
    expect(renderToString(h("div", null, raw("<b>bold</b>")))).toBe(
      "<div><b>bold</b></div>",
    );
  });
});

describe("attributes", () => {
  it("boolean true → bare attr; false/null/undefined → omitted", () => {
    expect(
      renderToString(
        h("input", { disabled: true, checked: false, hidden: null }),
      ),
    ).toBe("<input disabled>");
  });

  it("className→class, htmlFor→for; hx-*/ws-*/data-* verbatim", () => {
    expect(
      renderToString(
        h("label", {
          className: "c",
          htmlFor: "n",
          "hx-get": "/u",
          "ws-send": true,
          "data-x": "1",
        }),
      ),
    ).toBe('<label class="c" for="n" hx-get="/u" ws-send data-x="1"></label>');
  });

  it("number values serialize; non-primitive values are skipped", () => {
    expect(
      renderToString(
        h("div", { tabIndex: 0, onClick: () => {}, style: { a: 1 } }),
      ),
    ).toBe('<div tabIndex="0"></div>');
  });

  it("insertion order is preserved", () => {
    expect(renderToString(h("x", { b: "1", a: "2" }))).toBe(
      '<x b="1" a="2"></x>',
    );
  });
});

describe("structure", () => {
  it("void elements: no children, no closing tag, no self-close slash", () => {
    expect(renderToString(h("br", null))).toBe("<br>");
    expect(renderToString(h("img", { src: "/a.png" }))).toBe(
      '<img src="/a.png">',
    );
  });

  it("Fragment renders children only, no wrapper", () => {
    expect(renderToString(h(Fragment, null, "a", h("b", null, "c")))).toBe(
      "a<b>c</b>",
    );
  });

  it("nested elements and numeric children", () => {
    expect(
      renderToString(h("ul", null, h("li", null, 1), h("li", null, 2))),
    ).toBe("<ul><li>1</li><li>2</li></ul>");
  });

  it("sync components are invoked with props + children", () => {
    const Box = (p: Props) =>
      h("section", { class: "box" }, p.children as never);
    expect(renderToString(h(Box, null, "inside"))).toBe(
      '<section class="box">inside</section>',
    );
  });
});

describe("renderToStream", () => {
  it("yields the same bytes as renderToString", async () => {
    const tree = h(
      "main",
      { id: "root" },
      h("h1", null, "Vinyl"),
      h(Fragment, null, raw("<hr>"), "tail"),
    );
    expect(await collect(tree)).toBe(renderToString(tree));
  });
});

describe("M2 boundary: async components throw", () => {
  it("a thenable-returning component throws a directive error", () => {
    const Slow = () => Promise.resolve(h("div", null));
    expect(() => renderToString(h(Slow, null))).toThrow(/Suspense.*M3/);
  });
});
