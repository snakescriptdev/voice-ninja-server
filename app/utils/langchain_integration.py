from langchain_community.document_loaders import Docx2txtLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
import os
import fitz
from PIL import Image
import pytesseract
import io



embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
        )


def get_splits(content_list):
    try:
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        documents = []
        for content in content_list:
            documents.append(Document(
                page_content=content["text_content"],
                metadata={"source": content["file_path"]}
            ))
        
        splits = text_splitter.split_documents(documents)
        return splits
    
    except Exception as e:
        print(f"Error processing file {str(e)}")
        return None

    
def convert_to_vectorstore(splits, id):
    try:
        os.makedirs('vectorstore', exist_ok=True)
        vectorstore_path = f'vectorstore/{id}_vector_store/'
        os.makedirs(os.path.dirname(vectorstore_path), exist_ok=True)

        # Check if the vectorstore files exist
        if os.path.exists(os.path.join(vectorstore_path, "index.faiss")) and \
        os.path.exists(os.path.join(vectorstore_path, "index.pkl")):
            try:
                # Load existing vectorstore
                vectorstore = FAISS.load_local(vectorstore_path, embeddings, allow_dangerous_deserialization=True)
                print(f'Loaded existing FAISS vectorstore from {vectorstore_path}.')
                
                # Add new documents to the existing vectorstore
                vectorstore.add_documents(splits)
                print(f'Added new documents to existing vectorstore.')
            except Exception as e:
                print(f"Error loading existing vectorstore: {e}")
                print("Creating a new vectorstore with all documents.")
                vectorstore = FAISS.from_documents(splits, embeddings)
        else:
            print(f'Creating new FAISS vectorstore.')
            vectorstore = FAISS.from_documents(splits, embeddings)

        # Save the updated or new vectorstore
        vectorstore.save_local(vectorstore_path)
        print(f'Saved vectorstore to {vectorstore_path}.')

        return True,vectorstore_path
        
    except Exception as e:
        print(str(e))
        return False,None
        