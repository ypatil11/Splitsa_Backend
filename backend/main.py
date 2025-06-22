import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime
from io import BytesIO
from typing import List

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from PIL import Image
from starlette.concurrency import run_in_threadpool

from models.expenseRequest import ExpenseRequest
from utils.expenseCalculator import ExpenseCalculator
from utils.gemini import Gemini
from utils.splitwiseManager import SplitwiseManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Create FastAPI app with proper metadata
app = FastAPI(
    title="Splitwise Receipt API",
    description="API for processing receipts and creating Splitwise expenses",
    version="1.0.0",
)

# Add gzip compression for faster responses
app.add_middleware(GZipMiddleware, minimum_size=1000)

def get_splitwise_manager():
    """Create a new SplitwiseManager instance"""
    return SplitwiseManager()

def get_gemini():
    """Create a new Gemini instance"""
    return Gemini()

def clean_temp_files(file_path):
    """Background task to clean up temporary files"""
    if os.path.exists(file_path):
        os.remove(file_path)
        logger.debug(f"Cleaned up temporary file: {file_path}")


@app.get("/")
async def root():
    """Root endpoint to verify API is running"""
    return {"message": "Welcome to the Splitwise Receipt API", "status": "operational"}


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


async def optimize_image(file_content, max_size=(1000, 1000), quality=85):
    """Optimize image for faster processing"""
    try:
        image = Image.open(BytesIO(file_content))
        
        # Resize if needed (preserving aspect ratio)
        if image.width > max_size[0] or image.height > max_size[1]:
            image.thumbnail(max_size, Image.LANCZOS)
        
        # Save optimized image
        optimized = BytesIO()
        image.save(optimized, format=image.format or 'JPEG', quality=quality, optimize=True)
        return optimized.getvalue()
    except Exception as e:
        logger.warning(f"Image optimization failed, using original: {e}")
        return file_content


@app.get("/groups")
async def get_groups():
    """
    Get all Splitwise groups for the current user
    
    Returns:
        JSON with all user's Splitwise groups
    """
    logger.info("Fetching Splitwise groups")
    
    try:
        # Create new splitwise manager instance
        splitwise_manager = get_splitwise_manager()
        
        # Fetch groups using thread pool (since it's I/O bound)
        groups = await run_in_threadpool(
            splitwise_manager.getGroups
        )
        
        logger.info(f"Successfully retrieved {len(groups)} Splitwise groups")
        
        return {
            "status": "success",
            "groups": groups,
        }
        
    except Exception as e:
        logger.error(f"Error fetching Splitwise groups: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail={"status": "error", "message": f"Failed to retrieve groups: {str(e)}"}
        )

@app.post("/imageUpload")
async def upload_image(
    background_tasks: BackgroundTasks, 
    files: List[UploadFile] = File(...), 
    groupId: str = Form(...)
):
    """
    Upload multiple receipt images and extract information using Gemini
    
    Args:
        background_tasks: FastAPI background tasks
        files: List of receipt image files to process
        groupId: ID of the Splitwise group
        
    Returns:
        JSON with receipt data, group members, and the file paths
    """
    logger.info(f"Processing multi-image upload for group ID: {groupId}")
    
    # Validate input quickly before any processing
    if not files or len(files) == 0:
        raise HTTPException(status_code=400, detail="No files provided")
    
    # Check file count limit
    if len(files) > 5:  # Reasonable limit to prevent abuse
        raise HTTPException(status_code=400, detail="Maximum 5 images allowed per request")
    
    # Validate all files are images
    for file in files:
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"File {file.filename} is not an image")
    
    # Create new splitwise manager instance
    splitwise_manager = get_splitwise_manager()

    # Create img directory if it doesn't exist
    imgDir = os.path.join(os.getcwd(), "img")
    os.makedirs(imgDir, exist_ok=True)
    
    # Generate a common batch ID for related images
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    batch_id = uuid.uuid4().hex[:8]
    
    saved_files = []  # Track files for cleanup in case of errors
    
    try:
        # Process all files
        file_paths = []
        for i, file in enumerate(files):
            # Generate unique filename for each image
            file_id = uuid.uuid4().hex[:4]
            file_extension = os.path.splitext(file.filename)[1]
            new_filename = f"receipt_{timestamp}_{batch_id}_{i}_{file_id}{file_extension}"
            file_path = os.path.join(imgDir, new_filename)
            
            # Read and optimize file content
            file_content = await file.read()
            optimized_content = await optimize_image(file_content)
            
            # Write optimized image to disk
            with open(file_path, "wb") as buffer:
                buffer.write(optimized_content)
                
            file_paths.append(file_path)
            saved_files.append(file_path)
            logger.debug(f"Saved image {i+1}/{len(files)}: {file_path}")
        
        # Create fresh Gemini instance
        gemini = get_gemini()
        
        # Process all receipts using the correct parameter structure
        receipt_data = await run_in_threadpool(
            gemini.extractFromReceipt, 
            imagePath=file_paths if len(file_paths) > 1 else file_paths[0]
        )
        
        # Get group members
        try:
            group_id = splitwise_manager.groups.get(groupId, int(groupId))
            members = await run_in_threadpool(
                splitwise_manager.getUsersfromGroup,
                id=group_id
            )
        except Exception as e:
            logger.warning(f"Error fetching members for group {groupId}: {str(e)}")
            members = {}
        
        # Return the receipt data and file paths
        return {
            "receipt_data": receipt_data,
            "members": members,
            "receipt_paths": file_paths,
            "primary_receipt_path": file_paths[0] if file_paths else None,
            "image_count": len(file_paths),
            "status": "success"
        }

    except Exception as e:
        logger.error(f"Error processing receipts: {str(e)}", exc_info=True)
        
        # Clean up all saved files as background tasks
        for file_path in saved_files:
            background_tasks.add_task(clean_temp_files, file_path)
            
        raise HTTPException(status_code=400, detail=f"Error processing receipts: {str(e)}")


@app.post("/expenses")
async def create_expense(background_tasks: BackgroundTasks, expenseData: ExpenseRequest):
    """
    Create an expense in Splitwise with the provided details
    
    Args:
        background_tasks: FastAPI background tasks
        expenseData: The expense request data containing all details
        
    Returns:
        JSON with the expense ID or error details
    """
    logger.info(f"Creating expense: {expenseData}")
    
    try:
        # Create fresh instances for each request
        split = get_splitwise_manager()
        expense_calculator = ExpenseCalculator()

        # Validate the expense data
        total_owed = await run_in_threadpool(
            expense_calculator.validateExpenseData, 
            expenseData.userSplits
        )
        
        if not total_owed:
            raise HTTPException(status_code=400, detail="Invalid expense calculation")
        
        # Create the expense (I/O bound, run in thread pool)
        expense_id, errors = await run_in_threadpool(
            split.createExpense,
            groupId=expenseData.groupId,
            totalAmount=total_owed,
            description=expenseData.description,
            userSplits=expenseData.userSplits,
            receipt=expenseData.receiptPath
        )
        
        # Schedule receipt cleanup as a background task
        if os.path.exists(expenseData.receiptPath):
            background_tasks.add_task(clean_temp_files, expenseData.receiptPath)
        
        if expense_id:
            logger.info(f"Expense created successfully with ID: {expense_id}")
            return {
                "expense_id": expense_id,
                "status": "success",
                "message": "Expense created successfully"
            }
        else:
            logger.error(f"Failed to create expense: {errors}")
            return {
                "status": "error",
                "message": errors
            }
    
    except Exception as e:
        logger.error(f"Error creating expense: {str(e)}", exc_info=True)
        # Clean up receipt file as background task if it exists
        if hasattr(expenseData, 'receiptPath') and expenseData.receiptPath:
            background_tasks.add_task(clean_temp_files, expenseData.receiptPath)
        raise HTTPException(
            status_code=400, 
            detail={"status": "error", "message": f"Failed to create expense: {str(e)}"}
        )