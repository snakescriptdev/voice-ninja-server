from fastapi import APIRouter, HTTPException, Query, Response,Depends
from fastapi_sqlalchemy import db
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
from datetime import timedelta, date
from typing import Optional
from app_v2.databases.models import ConversationsModel, AgentModel, UnifiedAuthModel, WebAgentLeadModel
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.activity_logger import log_activity
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
import io
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer


security = HTTPBearer()

router = APIRouter(prefix="/api/v2/conversation", tags=["conversation"],dependencies=[Depends(security)])

# 1. List all conversations (paginated, user-specific, latest first)
@router.get("/user",openapi_extra={"security":[{"BearerAuth": []}]})
def list_user_conversations(
	page: int = Query(1, ge=1),
	page_size: int = Query(10, ge=1, le=100),
	search: Optional[str] = Query(None, description="Search by agent name or lead name"),
	date_after: Optional[date] = Query(None),
	date_before: Optional[date] = Query(None),
	call_status: Optional[CallStatusEnum] = Query(None),
	current_user: UnifiedAuthModel = Depends(require_active_user())
):
	with db():
		q = (
			db.session.query(ConversationsModel)
			.outerjoin(AgentModel, ConversationsModel.agent_id == AgentModel.id)
			.outerjoin(WebAgentLeadModel, WebAgentLeadModel.conversation_id == ConversationsModel.id)
			.options(joinedload(ConversationsModel.agent), joinedload(ConversationsModel.lead))
			.filter(ConversationsModel.user_id == current_user.id)
		)

		if search:
			q = q.filter(
				or_(
					AgentModel.agent_name.ilike(f"%{search}%"),
					WebAgentLeadModel.name.ilike(f"%{search}%")
				)
			)
			
		if date_after:
			q = q.filter(ConversationsModel.created_at >= date_after)
			
		if date_before:
			q = q.filter(ConversationsModel.created_at <= date_before)
			
		if call_status:
			q = q.filter(ConversationsModel.call_status == call_status)

		q = q.order_by(ConversationsModel.created_at.desc())

		total = q.count()
		conversations = q.offset((page-1)*page_size).limit(page_size).all()

		def seconds_to_timer(secs):
			if not secs:
				return "0:00"
			return str(timedelta(seconds=secs))[:-3] if secs >= 60 else f"0:{secs:02d}"

		results = []
		for conv in conversations:
			results.append({
				"id": conv.id,
				"date": conv.created_at.strftime("%Y-%m-%d"),
				"agent_name": getattr(conv.agent, "agent_name", None),
				"duration": seconds_to_timer(conv.duration),
				"messages": conv.message_count,
				"call_status": conv.call_status.name if conv.call_status else None,
				"lead_name": getattr(conv.lead, "name", None) if conv.lead else None,
			})

		return {
			"page": page,
			"page_size": page_size,
			"total": total,
			"conversations": results
		}

# 2. Get conversation audio (by internal id)

@router.get("/{conversation_id}/audio",openapi_extra={"security":[{"BearerAuth": []}]})
def get_conversation_audio(conversation_id: int,current_user:UnifiedAuthModel= Depends(require_active_user())):
	with db():
		conv = db.session.query(ConversationsModel).filter(ConversationsModel.id == conversation_id, ConversationsModel.user_id==current_user.id).first()
		if not conv or not conv.elevenlabs_conv_id:
			raise HTTPException(status_code=404, detail="Conversation not found")
		elevenlabs_conv_id = conv.elevenlabs_conv_id

	el_conv = ElevenLabsConversation()
	resp = el_conv.get_conversation_audio(elevenlabs_conv_id)
	if not resp.status or not resp.data:
		raise HTTPException(status_code=404, detail="Audio not found")
	# resp.data is expected to be bytes
	audio_content = resp.data.get("content")
	media_type = resp.data.get("content-type","audio/mpeg")
	if not audio_content:
		raise HTTPException(status_code=404,detail="audio content missing")
	return Response(content=audio_content,media_type=media_type)

# 3. Get conversation details (db + 11labs transcript)
@router.get("/{conversation_id}/details",openapi_extra={"security":[{"BearerAuth": []}]})
def get_conversation_details(conversation_id: int,current_user: UnifiedAuthModel = Depends(require_active_user())):
	with db():
		conv = db.session.query(ConversationsModel).options(
			joinedload(ConversationsModel.agent),
			joinedload(ConversationsModel.lead)
		).filter(ConversationsModel.id == conversation_id,ConversationsModel.user_id==current_user.id).first()
		if not conv:
			raise HTTPException(status_code=404, detail="Conversation not found")
		elevenlabs_conv_id = conv.elevenlabs_conv_id

	el_conv = ElevenLabsConversation()
	transcript = []
	if elevenlabs_conv_id:
		meta = el_conv.extract_conversation_metadata(elevenlabs_conv_id)
		transcript = meta.get("transcript", [])

	def seconds_to_timer(secs):
		if not secs:
			return "0:00"
		return str(timedelta(seconds=secs))[:-3] if secs >= 60 else f"0:{secs:02d}"

	return {
		"conversation_details": {
			"datetime": conv.created_at.isoformat(),
			"duration": seconds_to_timer(conv.duration),
			"messages": conv.message_count,
			"channel": conv.channel.name if conv.channel else None,
			"cost": conv.cost
		},
		"call_info": {
			"agent": getattr(conv.agent, "agent_name", None),
			"status": conv.call_status.name if conv.call_status else None,
			"lead": {
				"name": conv.lead.name,
				"email": conv.lead.email,
				"phone": conv.lead.phone,
				"custom_data": conv.lead.custom_data,
				"created_at": conv.lead.created_at.isoformat()
			} if conv.lead else None
		},
		"transcripts": transcript
	}

# 4. Delete conversation (atomic: 11labs + db)
@router.delete("/{conversation_id}",openapi_extra={"security":[{"BearerAuth": []}]})
def delete_conversation(conversation_id: int,current_user= Depends(require_active_user())):
	with db():
		conv = db.session.query(ConversationsModel).filter(ConversationsModel.id == conversation_id,ConversationsModel.user_id==current_user.id).first()
		if not conv or not conv.elevenlabs_conv_id:
			raise HTTPException(status_code=404, detail="Conversation not found")
		elevenlabs_conv_id = conv.elevenlabs_conv_id

		el_conv = ElevenLabsConversation()
		resp = el_conv.delete_conversation(elevenlabs_conv_id)
		if not resp.status:
			raise HTTPException(status_code=500, detail="Failed to delete conversation from ElevenLabs")
		try:
			db.session.delete(conv)
			db.session.commit()
			
			log_activity(
				user_id=current_user.id,
				event_type="conversation_deleted",
				description=f"Deleted conversation: {elevenlabs_conv_id}",
				metadata={"conversation_id": conversation_id, "elevenlabs_conv_id": elevenlabs_conv_id}
			)
		except Exception as e:
			db.session.rollback()
			raise HTTPException(status_code=500, detail="Failed to delete conversation from DB")
	return {"success": True}
