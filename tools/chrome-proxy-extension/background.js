chrome.webRequest.onAuthRequired.addListener(
  function (details) {
    return {
      authCredentials: {
        username: "spbisytqkz",
        password: "GuiSe08Bwcg2~ciC3b"
      }
    };
  },
  { urls: ["<all_urls>"] },
  ["blocking"]
);
