const API_URL = "https://wubhcgbdlh.execute-api.ca-west-1.amazonaws.com/generate";

(function initTheme(){
  const saved = localStorage.getItem("vovkan_theme");
  const theme = saved === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  updateThemeLabel(theme);
})();

function updateThemeLabel(theme){
  document.getElementById("themeLabel").textContent = theme === "light" ? "Light" : "Dark";
  document.getElementById("themeDot").classList.toggle("on", theme === "light");
}

function toggleTheme(){
  const current = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  const next = current === "light" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("vovkan_theme", next);
  updateThemeLabel(next);
}

async function generateStory(){
  const ask = document.getElementById("ask").value.trim();
  const output = document.getElementById("generation");
  const btn = document.getElementById("generateBtn");

  if (!ask){
    output.value = "Please type your ask first.";
    return;
  }

  btn.disabled = true;
  btn.textContent = "Generating...";
  output.value = "";

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "CP",
        sectionMode: "diag_repair",
        concern: ask,
        diagnosis: ask,
        repair: ask,
        comment: "Answer the ask directly in plain text.",
        extra: ask
      })
    });

    const data = await res.json();
    if (!res.ok){
      output.value = data.error ? `${data.error}${data.details ? `\n${data.details}` : ""}` : `HTTP ${res.status}`;
      return;
    }

    output.value = data.story || "";
  } catch (e){
    output.value = "Network error. Check API /generate availability.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate";
  }
}
