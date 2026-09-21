from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from .accounts import AccountEdit, PoolOptions, parse_accounts


class ImportAccounts(BaseModel):
    content: SecretStr
    name: str = Field(default='', max_length=80)


class AccountSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=2000)


class AccountAction(AccountSelection):
    action: str = Field(pattern='^(enable|disable|delete)$')


class BackupRequest(AccountSelection):
    password: SecretStr


class RestoreRequest(BaseModel):
    backup: dict
    password: SecretStr


def account_routes(pool):
    router = APIRouter(prefix='/api/accounts')

    @router.get('')
    def list_accounts():
        return pool.public()

    @router.post('/import')
    def import_accounts(body: ImportAccounts):
        return pool.import_tokens(parse_accounts(body.content.get_secret_value()), body.name)

    @router.post('/action')
    def account_action(body: AccountAction):
        return pool.action(body.ids, body.action)

    @router.post('/refresh')
    def refresh_accounts(body: AccountSelection):
        return pool.refresh(body.ids)

    @router.post('/options')
    def options(body: PoolOptions):
        return pool.options(body)

    @router.post('/backup')
    def backup(body: BackupRequest):
        return pool.backup(body.ids, body.password.get_secret_value())

    @router.post('/restore')
    def restore(body: RestoreRequest):
        return pool.restore(body.backup, body.password.get_secret_value())

    @router.post('/{account_id}')
    def edit(account_id: str, body: AccountEdit):
        return pool.edit(account_id, body)

    return router
