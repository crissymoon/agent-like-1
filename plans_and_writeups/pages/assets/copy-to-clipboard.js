
        (function() {
            function copyText(text) {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    return navigator.clipboard.writeText(text);
                }
                return new Promise(function(resolve, reject) {
                    try {
                        var field = document.createElement("textarea");
                        field.value = text;
                        field.setAttribute("readonly", "readonly");
                        field.style.position = "absolute";
                        field.style.left = "-9999px";
                        document.body.appendChild(field);
                        field.select();
                        document.execCommand("copy");
                        document.body.removeChild(field);
                        resolve();
                    } catch (err) {
                        reject(err);
                    }
                });
            }

            function clearCopyReset(button) {
                if (button._copyResetTimer) {
                    window.clearTimeout(button._copyResetTimer);
                    button._copyResetTimer = null;
                }
            }

            function setCopyState(button, copied) {
                clearCopyReset(button);
                if (copied) {
                    button.textContent = "Copied!";
                    button.setAttribute("aria-label", "Code copied");
                    button.classList.add("copied");
                    button.classList.add("copy-flash");
                    button._copyResetTimer = window.setTimeout(function() {
                        button.textContent = "Copy";
                        button.setAttribute("aria-label", "Copy code");
                        button.classList.remove("copied");
                        button.classList.remove("copy-flash");
                        button._copyResetTimer = null;
                    }, 2000);
                } else {
                    button.textContent = "Copy";
                    button.setAttribute("aria-label", "Copy code");
                    button.classList.remove("copied");
                    button.classList.remove("copy-flash");
                }
            }

            function showCopyToast() {
                var toast = document.getElementById("copyToast");
                if (!toast) {
                    toast = document.createElement("div");
                    toast.id = "copyToast";
                    toast.className = "copy-toast";
                    toast.textContent = "Copied to clipboard!";
                    document.body.appendChild(toast);
                }
                toast.classList.add("show");
                if (toast._hideTimer) {
                    window.clearTimeout(toast._hideTimer);
                    toast._hideTimer = null;
                }
                toast._hideTimer = window.setTimeout(function() {
                    toast.classList.remove("show");
                    toast._hideTimer = null;
                }, 2000);
            }

            var saved = localStorage.getItem("exportTheme");
            if (saved === "dark" || saved === null) {
                document.body.classList.add("dark");
                if (saved === null) localStorage.setItem("exportTheme", "dark");
            }

            document.getElementById("modeToggle").addEventListener("click", function() {
                var isDark = document.body.classList.toggle("dark");
                localStorage.setItem("exportTheme", isDark ? "dark" : "light");
            });

            Array.prototype.forEach.call(document.querySelectorAll(".code-copy-btn"), function(button) {
                button.addEventListener("click", function() {
                    var block = button.closest(".code-block");
                    var code = block ? block.querySelector("pre code") : null;
                    if (!code) return;
                    var raw = code.textContent;
                    var normalized = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
                    copyText(normalized).then(function() {
                        setCopyState(button, true);
                        showCopyToast();
                    }).catch(function() {
                        setCopyState(button, false);
                    });
                });
            });
        })();
    
