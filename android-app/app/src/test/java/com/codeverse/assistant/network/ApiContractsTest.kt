package com.codeverse.assistant.network

import com.google.gson.Gson
import org.junit.Assert.assertTrue
import org.junit.Test

class ApiContractsTest {

    @Test
    fun `DOMSnapshot and AgentChatRequest match FastAPI snake_case contract`() {
        val snapshot = DOMSnapshot(
            url = "https://scholarships.gov.in/apply",
            title = "NSP Application",
            stepIndicator = "Step 1 of 4",
            currentStepNumber = 1,
            totalSteps = 4,
            fields = listOf(
                DOMField(
                    elementKey = "field_01",
                    selector = "#pan_input",
                    tagName = "input",
                    inputType = "text",
                    label = "PAN Number",
                    boundingBox = DOMBoundingBox(10f, 20f, 300f, 48f)
                )
            ),
            buttons = listOf(
                DOMButton(
                    elementKey = "btn_01",
                    selector = "#btn-final-submit",
                    text = "Submit Application",
                    buttonType = "submit",
                    isPrimary = true
                )
            ),
            textContentSnippet = "Step 1 of 4"
        )
        val req = AgentChatRequest(
            userId = "11111111-1111-1111-1111-111111111111",
            message = "Inspect form",
            language = "en",
            currentUrl = snapshot.url,
            domSnapshot = snapshot
        )

        val json = Gson().toJson(req)
        assertTrue(json.contains("\"dom_snapshot\""))
        assertTrue(json.contains("\"element_key\":\"field_01\""))
        assertTrue(json.contains("\"bounding_box\""))
        assertTrue(json.contains("\"current_step_number\":1"))
        assertTrue(json.contains("\"text_content_snippet\":\"Step 1 of 4\""))
    }
}
