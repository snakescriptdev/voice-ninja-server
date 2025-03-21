// show passwordInput JS
document.addEventListener("DOMContentLoaded", function () {
    const showPassIcons = document.querySelectorAll(".show_pass_icon");
    
    showPassIcons.forEach(icon => {
        icon.addEventListener("click", function () {
            const passwordInput = this.previousElementSibling.previousElementSibling; 
            const img = this.querySelector("img");

            if (passwordInput.type === "password") {
                passwordInput.type = "text";
                img.style.opacity = "0.5";
            } else {
                passwordInput.type = "password";
                img.style.opacity = "1";
            }
        });
    });
});

// sidebarCollapse script
$(document).ready(function () {
  $(".sidebarCollapse").on("click", function () {
    $("#sidebar").toggleClass("active");
    $("body").toggleClass("open_menu");
  });
  $(".close_sidebar_btn").on("click", function () {
    $("#sidebar").removeClass("active");
    $("body").removeClass("open_menu");
  });
});

// sidebar active link script

document.addEventListener("DOMContentLoaded", function () {
  // Get current page URL
  let currentPage = window.location.pathname.split("/").pop();

  // Select all sidebar links
  let sidebarLinks = document.querySelectorAll(".sidebar_content_list a");

  sidebarLinks.forEach((link) => {
    // Get the href attribute of the link
    let linkHref = link.getAttribute("href");

    // Compare with current page and add active class
    if (linkHref === currentPage) {
      link.classList.add("active_link");
    } else {
      link.classList.remove("active_link");
    }
  });
});


// editHeading script
function editHeading() {
    let heading = document.getElementById("agent-name");
    let currentText = heading.childNodes[0].nodeValue.trim(); // Get current text

    let input = document.createElement("input");
    input.type = "text";
    input.className = "form-control"; // Changed from input.class to input.className
    input.value = currentText;
    
    input.onblur = function () { // Save the new text when input loses focus
        heading.innerHTML = input.value + ' <span onclick="editHeading()"><img src="http://dev.voiceninja.ai/static/Web/images/pencil-icon.svg" style="cursor: pointer;"></span>';
    };

    input.onkeydown = function(event) { // Handle enter key press
        if (event.key === "Enter") {
            heading.innerHTML = input.value + ' <span onclick="editHeading()"><img src="http://dev.voiceninja.ai/static/Web/images/pencil-icon.svg" style="cursor: pointer;"></span>';
        }
    };

    heading.innerHTML = ""; // Clear heading content
    heading.appendChild(input);
    input.focus(); // Focus on input
}



// sidebar active link script
document.addEventListener("DOMContentLoaded", function () {
    const editButton = document.querySelector('.edit_knowledge_base_btn'); // Edit button
    const backButton = document.querySelector('.page_back_arrow'); // Back button
    const knowledgeBaseBox = document.getElementById('knowledge-base-box');
    const editKnowledgeBase = document.getElementById('edit-knowledge-base');

    if (editButton && backButton && knowledgeBaseBox && editKnowledgeBase) {
        editButton.addEventListener("click", function () {
            knowledgeBaseBox.classList.add("d-none"); // Hide knowledge base box
            editKnowledgeBase.classList.remove("d-none"); // Show edit knowledge base
        });

        backButton.addEventListener("click", function (event) {
            event.preventDefault(); // Prevent default anchor behavior
            editKnowledgeBase.classList.add("d-none"); // Hide edit knowledge base
            knowledgeBaseBox.classList.remove("d-none"); // Show knowledge base box
        });
    }
  });

if (document.getElementById('uploadFile')) {
    document.getElementById('uploadFile').addEventListener('change', function(e) {
        var fileName = e.target.files[0].name;
    var fileNameElement = document.getElementById('fileName');
    var uploadLabel = document.getElementById('uploadLabel');

    fileNameElement.textContent = fileName;
    fileNameElement.classList.remove('d-none');
    uploadLabel.classList.add('d-none');
    });
}
