class ModelConfigQueries:
    async def config_lock(self, owner):
        await self.lock("model-config:" + owner)

    async def config_current(self, owner):
        return await self.one(
            "SELECT * FROM model_configurations WHERE user_id=%s AND active", (owner,)
        )

    async def config_get(self, owner, version):
        return await self.one(
            "SELECT * FROM model_configurations WHERE user_id=%s AND id=%s", (owner, version)
        )

    async def config_insert(self, owner, version, model, ciphertext):
        await self.execute(
            "UPDATE model_configurations SET active=false,updated_at=now() WHERE user_id=%s",
            (owner,),
        )
        await self.execute(
            "INSERT INTO model_configurations(id,user_id,model,ciphertext) VALUES (%s,%s,%s,%s)",
            (version, owner, model, ciphertext),
        )

    async def config_acquire(self, owner, version, reference):
        await self.execute(
            "INSERT INTO model_configuration_references(id,user_id,configuration_id) "
            "VALUES (%s,%s,%s)",
            (reference, owner, version),
        )

    async def config_release(self, reference):
        await self.execute("DELETE FROM model_configuration_references WHERE id=%s", (reference,))

    async def config_cleanup(self):
        await self.execute(
            "UPDATE model_configurations c SET ciphertext=NULL,updated_at=now() "
            "WHERE NOT active AND ciphertext IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM model_configuration_references r WHERE r.configuration_id=c.id)"
        )
