from app_v2.utils.elevenlabs.kb_utils import ElevenLabsKB
import time

def debug_rag():
    kb = ElevenLabsKB()
    
    # 1. Create a dummy text document
    print("1. Creating dummy doc...")
    res = kb.add_text_document("This is a test for RAG deletion.", "Debug RAG Doc 2")
    if not res.status:
        print(f"Failed to create doc: {res.error_message}")
        return
    
    doc_id = res.data.get("document_id")
    print(f"Doc ID: {doc_id}")
    
    # 2. Compute RAG Index
    print("2. Computing RAG index...")
    # Manually call the internal post if needed, but let's use the method
    # modifying method slightly to print response if I could, but I can't easily modify the installed lib
    # so I'll trust the return value
    rag_id = kb.compute_rag_index(doc_id)
    print(f"RAG Index ID returned: {rag_id}")
    
    if not rag_id:
        print("Failed to get RAG ID. Check logs.")
        # Try to delete doc and exit
        kb.delete_document(doc_id)
        return

    # 3. Wait a bit?
    time.sleep(2)
    
    # 4. Delete RAG Index
    print(f"3. Deleting RAG index {rag_id}...")
    del_res = kb.delete_rag_index(doc_id, rag_id)
    print(f"Delete result: {del_res}")
    
    if del_res:
         print("✅ RAG Index deleted successfully")
    else:
         print("❌ Failed to delete RAG Index")

    # 5. Delete Document
    print("4. Deleting document...")
    kb.delete_document(doc_id)
    print("Done.")

if __name__ == "__main__":
    debug_rag()
