// 導讀文字 → HTML：段落用 \n\n、條列用 "- "、術語用 [[term]]。

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  return esc(s).replace(/\[\[(.+?)\]\]/g, (_, t) => `<span class="term" tabindex="0" data-term="${t}">${t}</span>`);
}

function richText(text) {
  return String(text || "").split(/\n{2,}/).map(block => {
    // 同一段裡連續的 "- " 行變成清單，其他連續的行變成段落（例如「要注意：」後面直接接條列）
    const groups = [];
    for (const l of block.split("\n").map(l => l.trim()).filter(Boolean)) {
      const bullet = l.startsWith("- ");
      const last = groups[groups.length - 1];
      if (last && last.bullet === bullet) last.lines.push(l);
      else groups.push({ bullet, lines: [l] });
    }
    return groups.map(g => g.bullet
      ? "<ul>" + g.lines.map(l => `<li>${inline(l.slice(2))}</li>`).join("") + "</ul>"
      : `<p>${g.lines.map(inline).join("<br>")}</p>`).join("");
  }).join("");
}

function setupTooltips(root, glossary) {
  const tip = document.getElementById("tip");
  const byTerm = new Map(glossary.map(t => [t.term.toLowerCase(), t]));
  const show = e => {
    const el = e.target.closest(".term");
    if (!el || el.closest("dt")) return;
    const t = byTerm.get(el.textContent.toLowerCase());
    tip.innerHTML = t ? `<strong>${esc(t.zh)}</strong>${esc(t.gloss)}` : "術語表裡沒有這個詞";
    tip.hidden = false;
    const r = el.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left), innerWidth - tip.offsetWidth - 8);
    const below = r.bottom + 6 + tip.offsetHeight < innerHeight;
    tip.style.left = left + "px";
    tip.style.top = (below ? r.bottom + 6 : r.top - tip.offsetHeight - 6) + "px";
  };
  const hide = e => { if (e.target.closest(".term")) tip.hidden = true; };
  root.addEventListener("mouseover", show);
  root.addEventListener("focusin", show);
  root.addEventListener("mouseout", hide);
  root.addEventListener("focusout", hide);
}

if (typeof module !== "undefined") module.exports = { esc, richText };
