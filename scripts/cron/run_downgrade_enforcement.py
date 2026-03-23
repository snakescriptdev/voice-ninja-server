import sys
import os
from datetime import datetime
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../.env')))

def run_downgrade_enforcement():
    """
    Cron job script to process scheduled downgrades.
    """
    print(f"[{datetime.utcnow()}] Starting scheduled downgrade enforcement...")
    
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app_v2.core.config import VoiceSettings
    from app_v2.databases.models import ScheduledDowngradeModel, ActivityLogModel, AgentModel, KnowledgeBaseModel, AgentKnowledgeBaseBridge
    from app_v2.schemas.enum_types import ScheduledDowngradeStatusEnum
    from app_v2.utils.downgrade_utils import enforce_downgrade_for_user
    from app_v2.utils.elevenlabs import ElevenLabsAgent
    
    engine = create_engine(VoiceSettings.DB_URL)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        now = datetime.utcnow()
        # Find pending downgrades scheduled for now or earlier
        pending = (
            session.query(ScheduledDowngradeModel)
            .filter(
                ScheduledDowngradeModel.status == ScheduledDowngradeStatusEnum.pending,
                ScheduledDowngradeModel.scheduled_for <= now
            )
            .all()
        )

        print(f"[{datetime.utcnow()}] Found {len(pending)} pending downgrades to process.")

        for dg in pending:
            try:
                print(f"[{datetime.utcnow()}] Processing downgrade id={dg.id} for user={dg.user_id}")
                
                # Execute enforcement
                summary = enforce_downgrade_for_user(
                    user_id=dg.user_id,
                    old_plan_id=dg.old_plan_id,
                    new_plan_id=dg.new_plan_id,
                    session=session
                )
                
                # Update status
                dg.status = ScheduledDowngradeStatusEnum.completed
                dg.executed_at = datetime.utcnow()
                
                # Log activity manually (since log_activity depends on fastapi_sqlalchemy.db)
                activity = ActivityLogModel(
                    user_id=dg.user_id,
                    event_type="subscription_downgrade_enforced",
                    description=(
                        f"Scheduled downgrade from {dg.old_plan_id} to {dg.new_plan_id} "
                        f"has been enforced."
                    ),
                    metadata_json={
                        "scheduled_downgrade_id": dg.id,
                        "old_plan_id": dg.old_plan_id,
                        "new_plan_id": dg.new_plan_id,
                        "enforcement_summary": summary,
                    },
                )
                session.add(activity)

                # Sync affected agents with ElevenLabs if KB was affected
                if "knowledge_base" in summary:
                    agent_ids = summary["knowledge_base"].get("agent_ids_to_sync", [])
                    for agent_id in agent_ids:
                        try:
                            # Inline sync logic to avoid fastapi_sqlalchemy dependency
                            agent = session.query(AgentModel).filter(AgentModel.id == agent_id).first()
                            if agent and agent.elevenlabs_agent_id:
                                all_kb = (
                                    session.query(KnowledgeBaseModel)
                                    .join(AgentKnowledgeBaseBridge)
                                    .filter(AgentKnowledgeBaseBridge.agent_id == agent_id, KnowledgeBaseModel.elevenlabs_document_id.isnot(None))
                                    .all()
                                )
                                kb_docs = []
                                for item in all_kb:
                                    doc_type = "file" if item.kb_type == "file" else "url" if item.kb_type == "url" else "text"
                                    kb_docs.append({
                                        "id": item.elevenlabs_document_id,
                                        "name": item.title or "Untitled",
                                        "type": doc_type,
                                        "usage_mode": "auto"
                                    })
                                ElevenLabsAgent().update_agent(
                                    agent_id=agent.elevenlabs_agent_id,
                                    knowledge_base=kb_docs
                                )
                                print(f"[{datetime.utcnow()}] Synced agent={agent_id} with ElevenLabs after KB downgrade")
                        except Exception as se:
                            print(f"[{datetime.utcnow()}] Failed to sync agent={agent_id}: {se}")

                session.commit()
                print(f"[{datetime.utcnow()}] Successfully enforced downgrade id={dg.id}")

            except Exception as e:
                session.rollback()
                dg.status = ScheduledDowngradeStatusEnum.failed
                dg.error_message = str(e)
                session.commit()
                print(f"[{datetime.utcnow()}] Failed to enforce downgrade id={dg.id}: {e}")

        print(f"[{datetime.utcnow()}] Scheduled downgrade enforcement completed.")

    except Exception as e:
        print(f"Error during downgrade enforcement: {e}")
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    run_downgrade_enforcement()
