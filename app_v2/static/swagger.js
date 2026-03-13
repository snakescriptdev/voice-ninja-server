document.addEventListener("click", function(e){

const summary = e.target.closest(".opblock-summary");

if(!summary) return;

const block = summary.parentElement;

block.style.transition = "all .3s ease";

});