let mediaRecorder;
let audioChunks = [];

const startBtn = document.getElementById("start-record-btn");
const recordingIndicator = document.getElementById("recording-indicator");
const voicePreview = document.getElementById("voice-preview");
const reRecordBtn = document.getElementById("re-record-btn");
const createVoiceForm = document.getElementById("create-voice-form");
const host = window.location.origin;


const searchInput = document.getElementById("voice-search");
const tableBody = document.querySelector("#voices-table tbody");
const paginationContainer = document.querySelector(".table_pagination .pagination");

let currentPage = parseInt(new URLSearchParams(window.location.search).get("page")) || 1;


function showOrHideLoader(show){
    let loaderEle = document.querySelector(".loading-state");
    if(loaderEle){
        show? loaderEle.style.display = "flex" : loaderEle.style.display = "none";
    }
}

function handleOpenEditVoiceModal(element){
    const voiceId = element.dataset.voiceId;
    const voiceName = element.dataset.voiceName;
    document.getElementById("edit-voice-id").value = voiceId;
    document.getElementById("edit-voice-name").value = voiceName;
    $("#editVoiceModal").modal("show");
}

function handleCloseEditModal(){
    $("#editVoiceModal").modal("hide");
}

async function handleEditVoice(e){
    try{
        e.preventDefault();
        showOrHideLoader(true);
        const voiceId = document.getElementById("edit-voice-id").value;
        const voiceName = document.getElementById("edit-voice-name").value.trim();

        const resp = await fetch(`${host}/api/edit_voice`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({voice_id: parseInt(voiceId), voice_name: voiceName})
        });
        const data = await resp.json();
        if(data.status){
            toastr.success(data.message);
            const eleBtnEle = document.getElementById(`edit-voice-btn-${voiceId}`);
            eleBtnEle.setAttribute("data-voice-name",voiceName);
            const deleteBtnEle = document.getElementById(`delete-voice-btn-${voiceId}`);
            deleteBtnEle.setAttribute("data-voice-name",voiceName);
            document.querySelector(`#voice-row-${voiceId} [data-field="voiceName"]`).textContent = voiceName;
            $("#editVoiceModal").modal("hide");
        } else toastr.error(data.message);
    }
    catch(error){
        console.error({error});
        toastr.error("Some Error in editing voice name. Please contact support team.")
    }
    finally{
        showOrHideLoader(false);
    }
}

function handleDeleteVoice(element){
    try{
        showOrHideLoader(true);
        const voiceId = element.dataset.voiceId;
        const voiceName = element.dataset.voiceName;
        document.getElementById("delete-voice-name").innerText = voiceName;
        const modal = new bootstrap.Modal(document.getElementById("deleteVoiceModal"));
        modal.show();

        document.getElementById("confirm-delete-voice").onclick = async function(){
            const resp = await fetch(`${host}/api/delete_voice/?voice_id=${voiceId}`, {method: "DELETE"});
            const data = await resp.json();
            if(data.status){
                toastr.success(data.message);
                modal.hide();
                setTimeout(()=> location.reload(), 1000);
            } else toastr.error(data.message);
        };
    }
    catch(error){
        console.error({error});
        toastr.error("Some Error in deleting voice. Please contact support team.")
    }
    finally{
        showOrHideLoader(false);
    }
}

startBtn.addEventListener("click", async () => {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorder = new MediaRecorder(stream);
        audioChunks = [];

        mediaRecorder.ondataavailable = e => audioChunks.push(e.data);
        mediaRecorder.onstop = handleRecordingStop;

        mediaRecorder.start();
        startBtn.style.display = "none";
        recordingIndicator.style.display = "block";

        setTimeout(() => mediaRecorder.stop(), 10000);
    } catch (err) {
        toastr.error("Microphone access denied or error: " + err.message);
    }
});

function handleRecordingStop() {
    recordingIndicator.style.display = "none";

    const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
    const audioUrl = URL.createObjectURL(audioBlob);

    voicePreview.src = audioUrl;
    voicePreview.style.display = "block";
    reRecordBtn.style.display = "inline-block";
    createVoiceForm.style.display = "block";

    createVoiceForm.onsubmit = async (e) => {
        try {
            showOrHideLoader(true);
            e.preventDefault();
            const voiceName = document.getElementById("voice-name").value.trim();
            if (!voiceName) {
                toastr.error("Please enter a voice name.");
                return;
            }

            const formData = new FormData();
            formData.append("voice_name", voiceName);
            formData.append("audio_file", audioBlob, "voice.webm");
        
            const resp = await fetch(`${host}/api/create_voice`, { method: "POST", body: formData });
            const data = await resp.json();
            if (data.status) {
                toastr.success(data.message);
                new bootstrap.Modal(document.getElementById("createVoiceModal")).hide();
                setTimeout(() => location.reload(), 1000);
            } else {
                toastr.error(data.message);
            }
        } 
        catch (err) {
            toastr.error("Error uploading voice: " + err.message);
        }
        finally{
            showOrHideLoader(false);
        }
    };
}

reRecordBtn.addEventListener("click", () => {
    voicePreview.style.display = "none";
    reRecordBtn.style.display = "none";
    createVoiceForm.style.display = "none";
    startBtn.style.display = "inline-block";
});


async function fetchVoices(query = "", page = 1) {
    try{
        showOrHideLoader(true);
        const host = window.location.origin;
        // Build URL with query params
        const url = new URL(`${host}/custom-voice-dashboard`);
        url.searchParams.append("page", page);
        if(query) url.searchParams.append("search", query);

        const resp = await fetch(url);
        const html = await resp.text();

        // Parse returned HTML and replace table body & pagination
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");

        const newTbody = doc.querySelector("#voices-table tbody");
        const newPagination = doc.querySelector(".table_pagination .pagination");

        if(newTbody && newPagination){
            tableBody.innerHTML = newTbody.innerHTML;
            paginationContainer.innerHTML = newPagination.innerHTML;
        }
    }
    catch(error){
        console.error({error});
    }
    finally{
        showOrHideLoader(false);
    }
}

searchInput.addEventListener("input", function(e) {
    const query = e.target.value.trim();
    currentPage = 1;
    fetchVoices(query, currentPage);
});

paginationContainer.addEventListener("click", function(e){
    e.preventDefault();
    const target = e.target.closest("a.page-link");
    if(!target) return;

    const urlParams = new URLSearchParams(target.getAttribute("href").split("?")[1]);
    const page = urlParams.get("page") || 1;
    const query = searchInput.value.trim();
    currentPage = parseInt(page);
    fetchVoices(query, currentPage);
})


function openDeleteVoiceModal(){
    $("#deleteVoiceModal").modal("show");
}

function handleCloseDeleteVoiceModal(){
    $("#deleteVoiceModal").modal("hide");
}

fetchVoices(); //for initial loading