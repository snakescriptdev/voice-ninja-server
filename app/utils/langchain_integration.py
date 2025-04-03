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
        unique_id = id
        vectorstore_path = f'vectorstore/{id}_vector_store/'
        os.makedirs(os.path.dirname(vectorstore_path), exist_ok=True)

        # Check if the vectorstore files exist
        if os.path.exists(os.path.join(vectorstore_path, f"{unique_id}_index.faiss")) and \
        os.path.exists(os.path.join(vectorstore_path, f"{unique_id}_index.pkl")):
            try:
                # Load existing vectorstore
                vectorstore = FAISS.load_local(vectorstore_path, embeddings, allow_dangerous_deserialization=True)
                # Add new documents to the existing vectorstore
                vectorstore.add_documents(splits)
            except Exception as e:
                print(f"Error loading existing vectorstore: {e}")
                vectorstore = FAISS.from_documents(splits, embeddings)
        else:
            vectorstore = FAISS.from_documents(splits, embeddings)

        # Save with unique filenames
        vectorstore.save_local(vectorstore_path, index_name=f"{unique_id}_index")
        return True,vectorstore_path
        
    except Exception as e:
        print(str(e))
        return False,None
    
async def retrieve_from_vectorstore(query: str, vectorstore_path: str, id: str, top_k: int = 5) -> list:
    """
    Retrieves relevant documents from the vectorstore based on a query.
    
    Args:
        query (str): The search query
        vectorstore_path (str): Path to the vectorstore
        id (str): Unique identifier for the vectorstore
        top_k (int): Number of results to return (default 5)
        
    Returns:
        list: List of relevant document chunks with their scores
    """
    try:
        # Load the vectorstore
        if not os.path.exists(vectorstore_path):
            print(f"Vectorstore not found at {vectorstore_path}")
            return []
            
        vectorstore = FAISS.load_local(
            vectorstore_path,
            embeddings,
            allow_dangerous_deserialization=True,
            index_name=f"{id}_index"
        )
        
        # Perform similarity search
        results = vectorstore.similarity_search_with_score(query, k=top_k)
        
        # Format results
        formatted_results = []
        for doc, score in results:
            # Extract relevant fields from document and format for response
            formatted_result = {
                'content': doc.page_content,
                'metadata': {
                    'source': doc.metadata.get('source', ''),
                    'id': doc.metadata.get('id', '')
                },
                'score': float(score)
            }
            formatted_results.append(formatted_result)
            
        # Sort results by score ascending (lower score = better match)
        formatted_results.sort(key=lambda x: x['score'])
            
        return formatted_results
        
    except Exception as e:
        print(f"Error retrieving from vectorstore: {str(e)}")
        return []
