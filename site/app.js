// The site does not read local files, store user data, or call application APIs.
const downloadLinks = document.querySelectorAll(".download-file");
const notice = document.querySelector("#download-notice");
let noticeTimer;
downloadLinks.forEach((link) => link.addEventListener("click", (event) => {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  clearTimeout(noticeTimer);
  notice.hidden = false;
  // A link click cannot confirm completion of a browser-managed download.
  noticeTimer = setTimeout(() => {
    notice.hidden = true;
  }, 4200);
}));
