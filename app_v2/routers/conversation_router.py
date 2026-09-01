from fastapi import APIRouter, HTTPException, Query, Response,Depends
from fastapi_sqlalchemy import db
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
from datetime import date
from typing import Optional
from app_v2.databases.models import ConversationsModel, AgentModel, UnifiedAuthModel, WidgetLeadModel
from app_v2.utils.elevenlabs.conversation_utils import ElevenLabsConversation
from app_v2.utils.activity_logger import log_activity
from app_v2.schemas.enum_types import CallStatusEnum, ChannelEnum
import io
from app_v2.utils.jwt_utils import require_active_user, HTTPBearer
from app_v2.schemas.pagination import PageSize
from sqlalchemy import desc
from app_v2.routers.internal_reconciliation import reconcile_conversation_row, reconcile_conversation_rows
from app_v2.utils.conversation_lifecycle import release_conversation_finalize_claim

security = HTTPBearer()

router = APIRouter(prefix="/api/v2/conversation", tags=["conversation"],dependencies=[Depends(security)])

# 1. List all conversations (paginated, user-specific, latest first)
@router.get("/user",openapi_extra={"security":[{"BearerAuth": []}]})
def list_user_conversations(
	page: int = Query(1, ge=1),
	page_size: PageSize = 10,
	search: Optional[str] = Query(None, description="Search by agent name or lead name"),
	date_after: Optional[date] = Query(None),
	date_before: Optional[date] = Query(None),
	call_status: Optional[CallStatusEnum] = Query(None),
	channel: Optional[ChannelEnum] = Query(None),
	agent_id: Optional[int] = Query(None, description="Filter to a single agent's conversations"),
	low_balance_only: bool = Query(False, description="Only show calls that ended due to low coins balance"),
	current_user: UnifiedAuthModel = Depends(require_active_user())
):
	with db():
		q = (
			db.session.query(ConversationsModel)
			.outerjoin(AgentModel, ConversationsModel.agent_id == AgentModel.id)
			.outerjoin(WidgetLeadModel, WidgetLeadModel.conversation_id == ConversationsModel.id)
			.options(
				joinedload(ConversationsModel.agent),
				joinedload(ConversationsModel.lead),
			)
			.filter(ConversationsModel.user_id == current_user.id)
		)

		if search:
			q = q.filter(
				or_(
					AgentModel.agent_name.ilike(f"%{search}%"),
					WidgetLeadModel.name.ilike(f"%{search}%")
				)
			)
			
		if date_after:
			q = q.filter(ConversationsModel.created_at >= date_after)
			
		if date_before:
			q = q.filter(ConversationsModel.created_at <= date_before)
			
		if call_status:
			q = q.filter(ConversationsModel.call_status == call_status)

		if channel:
			q = q.filter(ConversationsModel.channel == channel)

		if agent_id is not None:
			q = q.filter(ConversationsModel.agent_id == agent_id)

		if low_balance_only:
			q = q.filter(ConversationsModel.ended_due_to_low_balance.is_(True))

		q = q.order_by(ConversationsModel.created_at.desc())

		total = q.count()
		conversations = q.offset((page-1)*page_size).limit(page_size).all()

		def seconds_to_timer(secs):
			if not secs:
				return "00:00:00"
			secs = int(secs)
			hours, remainder = divmod(secs, 3600)
			minutes, seconds = divmod(remainder, 60)
			return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
		
		
		results = []
		for conv in conversations:
			results.append({
				"id": conv.id,
				"datetime": conv.created_at.isoformat(),
				"date": conv.created_at.strftime("%b %d, %Y"),
				"time": conv.created_at.strftime("%I:%M %p"),
				"agent_id": conv.agent_id,
				"agent_name": getattr(conv.agent, "agent_name", None),
				"duration": seconds_to_timer(conv.duration),
				"messages": conv.message_count,
				"call_status": conv.call_status.name if conv.call_status else None,
				"channel": conv.channel.value if conv.channel else None,
				"lead_name": getattr(conv.lead, "name", None) if conv.lead else None,
				"cost": conv.cost_inr or 0,
				"ended_due_to_low_balance": conv.ended_due_to_low_balance,
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

def _seconds_to_timer(secs):
	if not secs:
		return "00:00:00"
	secs = int(secs)
	hours, remainder = divmod(secs, 3600)
	minutes, seconds = divmod(remainder, 60)
	return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _serialize_conversation_details(conv: ConversationsModel, transcript: list) -> dict:
	return {
		"conversation_details": {
			"datetime": conv.created_at.isoformat(),
			"duration": _seconds_to_timer(conv.duration),
			"messages": conv.message_count,
			"channel": conv.channel.value if conv.channel else None,
			"cost": conv.cost_inr or 0,
            "error_message": conv.error_message,
            "ended_due_to_low_balance": conv.ended_due_to_low_balance,
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


# 3. Get conversation details (db + 11labs transcript)
@router.get("/{conversation_id}/details",openapi_extra={"security":[{"BearerAuth": []}]})
def get_conversation_details(conversation_id: int,current_user: UnifiedAuthModel = Depends(require_active_user())):
	with db():
		conv = db.session.query(ConversationsModel).options(
			joinedload(ConversationsModel.agent),
			joinedload(ConversationsModel.lead),
		).filter(ConversationsModel.id == conversation_id,ConversationsModel.user_id==current_user.id).first()
		if not conv:
			raise HTTPException(status_code=404, detail="Conversation not found")
		elevenlabs_conv_id = conv.elevenlabs_conv_id

	el_conv = ElevenLabsConversation()
	transcript = []
	if elevenlabs_conv_id:
		# Shorter budget than the default: this runs synchronously in an HTTP
		# request (unlike the finalize-call flows, which retry via
		# asyncio.to_thread), so it shouldn't block the details page for the
		# full retry window just to fetch a transcript for display.
		meta = el_conv.extract_conversation_metadata(elevenlabs_conv_id, max_retries=5, delay_seconds=3.0)
		transcript = meta.get("transcript", [])

	return _serialize_conversation_details(conv, transcript)


# 3b. Retry/refetch a stuck call — re-runs the exact same status-check +
# metadata-retry + finalize pipeline reconcile_stuck_calls() uses for calls
# whose process crashed before the live flow could finalize them, but
# on-demand for a single conversation the owning user is looking at, instead
# of waiting for the next cron pass.
@router.post("/{conversation_id}/retry",openapi_extra={"security":[{"BearerAuth": []}]})
async def retry_conversation(conversation_id: int,current_user: UnifiedAuthModel = Depends(require_active_user())):
	with db():
		conv = db.session.query(ConversationsModel).options(
			joinedload(ConversationsModel.agent),
			joinedload(ConversationsModel.lead),
		).filter(ConversationsModel.id == conversation_id,ConversationsModel.user_id==current_user.id).first()
		if not conv:
			raise HTTPException(status_code=404, detail="Conversation not found")
		if not conv.elevenlabs_conv_id:
			raise HTTPException(status_code=400, detail="Call has no ElevenLabs conversation to retry")

		is_in_progress = conv.call_status == CallStatusEnum.in_progress
		elevenlabs_conv_id = conv.elevenlabs_conv_id
		channel = conv.channel
		agent_id = conv.agent_id

	if not is_in_progress:
		# Already finalized — finalize_conversation() deducts coins on every
		# call, so it must never run twice for the same row. Just refresh
		# the transcript/details for display instead.
		el_conv = ElevenLabsConversation()
		meta = el_conv.extract_conversation_metadata(elevenlabs_conv_id, max_retries=5, delay_seconds=3.0)
		with db():
			conv = db.session.query(ConversationsModel).options(
				joinedload(ConversationsModel.agent),
				joinedload(ConversationsModel.lead),
			).filter(ConversationsModel.id == conversation_id).first()
		return {"outcome": "already_finalized", **_serialize_conversation_details(conv, meta.get("transcript", []))}

	# Unconditionally clear any leftover finalize claim before retrying — safe
	# here because this is a deliberate, user-triggered action, not a
	# background race. Recovers a row that got stuck mid-claim (e.g. a hard
	# process crash between claiming and finishing finalize), which neither
	# the webhook nor the cron sweep could otherwise ever reclaim.
	with db():
		release_conversation_finalize_claim(conversation_id)

	el_conv = ElevenLabsConversation()
	result = await reconcile_conversation_row(conversation_id, elevenlabs_conv_id, channel, agent_id, el_conv)

	with db():
		conv = db.session.query(ConversationsModel).options(
			joinedload(ConversationsModel.agent),
			joinedload(ConversationsModel.lead),
		).filter(ConversationsModel.id == conversation_id).first()
		if not conv:
			raise HTTPException(status_code=404, detail="Conversation not found")

	transcript = result.get("metadata", {}).get("transcript", []) if result.get("outcome") == "finalized" else []
	return {
		"outcome": result.get("outcome"),
		"error": result.get("error"),
		**_serialize_conversation_details(conv, transcript),
	}


# 3c. Retry/refetch ALL of the current user's stuck "in progress" calls in
# one go — same reconcile_conversation_row()/reconcile_conversation_rows()
# pipeline as the single-conversation retry above and the
# reconcile_stuck_calls() cron, just scoped by user_id instead of by a
# specific id or the internal-secret cron auth.
MAX_USER_RETRY_ROWS = 25

@router.post("/retry-in-progress",openapi_extra={"security":[{"BearerAuth": []}]})
async def retry_in_progress_conversations(current_user: UnifiedAuthModel = Depends(require_active_user())):
	with db():
		stuck = (
			db.session.query(ConversationsModel)
			.filter(
				ConversationsModel.user_id == current_user.id,
				ConversationsModel.call_status == CallStatusEnum.in_progress,
				ConversationsModel.elevenlabs_conv_id.isnot(None),
			)
			.order_by(ConversationsModel.created_at.asc())
			.limit(MAX_USER_RETRY_ROWS)
			.all()
		)
		# Snapshot the handful of fields we need — the rows themselves get
		# detached the moment this `with db():` block closes.
		rows = [(r.id, r.elevenlabs_conv_id, r.channel, r.agent_id) for r in stuck]

		# Unconditionally clear any leftover finalize claim on these rows
		# before retrying — see the single-conversation retry above for why
		# this is safe only because it's a deliberate, user-triggered action.
		for row_id, _, _, _ in rows:
			release_conversation_finalize_claim(row_id)

	el_conv = ElevenLabsConversation()
	return await reconcile_conversation_rows(rows, el_conv)

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
			raise HTTPException(status_code=500, detail="Failed to delete conversation")
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
