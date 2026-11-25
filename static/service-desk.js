"use strict";

const category = document.querySelector("[data-suggestions]");
const suggestions = document.querySelector("#kb-suggestions");
let pending;

async function suggest() {
  pending?.abort();
  suggestions.replaceChildren();
  suggestions.hidden = true;
  if (!category.value) return;
  pending = new AbortController();
  const { signal } = pending;
  const endpoint = new URL(category.dataset.suggestions, window.location.origin);
  endpoint.searchParams.set("category", category.value);
  try {
    const result = await fetch(endpoint, { signal });
    if (!result.ok || result.redirected) return;
    const items = await result.json();
    if (signal.aborted || !items.length) return;
    const label = document.createElement("strong");
    label.textContent = "Artigos sugeridos";
    const list = document.createElement("ul");
    for (const item of items) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.className = "text-link";
      link.href = item.url;
      link.textContent = item.title;
      li.append(link);
      list.append(li);
    }
    suggestions.replaceChildren(label, list);
    suggestions.hidden = false;
  } catch (error) {
    if (error.name !== "AbortError") suggestions.hidden = true;
  }
}

if (category && suggestions) {
  category.addEventListener("change", suggest);
  if (category.value) suggest();
}

document.querySelectorAll("[data-macro]").forEach((select) => {
  select.addEventListener("change", () => {
    const field = document.getElementById(select.dataset.target);
    if (!select.value || !field) return;
    if (field.value.trim() && !window.confirm("Substituir o texto pela resposta selecionada?")) {
      select.value = "";
      return;
    }
    field.value = select.value;
    select.value = "";
    field.focus();
  });
});
