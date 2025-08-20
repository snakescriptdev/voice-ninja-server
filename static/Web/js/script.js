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
    input.className = "form-control";
    input.value = currentText;
    
    // Create update function to avoid duplicate code
    const updateHeading = (value) => {
        const currentUrl = new URL(window.location.href);
        const agentId = currentUrl.searchParams.get("agent_id");

        // Call API to update agent name
        fetch(`${host}/api/update-agent`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCSRFToken()
            },
            body: JSON.stringify({
                agent_id: agentId,
                agent_name: value
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === "success") {
                const span = document.createElement('span');
                span.onclick = editHeading;
                span.className = "edit-heading-icon";
                const img = document.createElement('img');
                img.src = "/static/Web/images/pencil-icon.svg";
                img.style.cursor = "pointer";
                span.appendChild(img);
                
                heading.textContent = value + ' ';
                heading.appendChild(span);
                toastr.success('Agent name updated successfully');
            } else {
                toastr.error(data.message || 'Error updating agent name');
            }
        })
        .catch(error => {
            toastr.error('Error updating agent name');
            console.error('Error:', error);
        });
    };

    input.onblur = function() {
        updateHeading(this.value);
    };

    input.onkeydown = function(event) {
        if (event.key === "Enter") {
            updateHeading(this.value);
        }
    };

    heading.textContent = ""; // Use textContent instead of innerHTML
    heading.appendChild(input);
    input.focus();
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

function getCSRFToken() {
    let token = null;
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
        token = meta.getAttribute('content');
    }

    if (!token) {
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input) {
            token = input.value;
        }
    }

    return token;
}
