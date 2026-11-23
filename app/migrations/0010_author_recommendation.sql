-- 0010：证据图谱来源性质增加“报告建议”，区分作者建议与机构已作出的决定。
-- 只做前向迁移；旧快照的 claim_nature 保持原值，不回填。

ALTER TABLE graph_edges DROP CONSTRAINT graph_edges_claim_nature_check;

ALTER TABLE graph_edges
    ADD CONSTRAINT graph_edges_claim_nature_check CHECK (
        claim_nature IS NULL OR claim_nature IN (
            'media_report',
            'party_claim',
            'author_recommendation',
            'official_decision',
            'other_material'
        )
    );
