package io.apm.bdi;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import jason.architecture.AgArch;
import jason.asSemantics.ActionExec;
import jason.asSemantics.Agent;
import jason.asSemantics.Circumstance;
import jason.asSemantics.Message;
import jason.asSemantics.TransitionSystem;
import jason.asSyntax.Literal;
import jason.asSyntax.Structure;
import jason.runtime.Settings;

/**
 * Minimal custom agent architecture that uses Jason only as the BDI engine.
 * SPADE remains the communication/runtime framework of AgenticProactiveMonitor.
 */
public final class TechnicalLeadDecisionRunner extends AgArch {
    private static final int MAX_REASONING_CYCLES = 64;

    private final List<Literal> percepts = new ArrayList<>();
    private String selectedIncidentId;
    private String primaryInvestigator;

    private TechnicalLeadDecisionRunner(
            String aslPath,
            String incidentId,
            String probableDomain,
            String recommendedAgent,
            Set<String> availableAgents) throws Exception {
        Agent agent = new Agent();
        new TransitionSystem(agent, new Circumstance(), new Settings(), this);
        agent.initAg(aslPath);

        String incident = quote(incidentId);
        percepts.add(Literal.parseLiteral("incident(" + incident + ")"));
        percepts.add(Literal.parseLiteral(
                "incident_status(" + incident + ",taken_in_charge)"));
        percepts.add(Literal.parseLiteral(
                "triage_complete(" + incident + ")"));
        percepts.add(Literal.parseLiteral(
                "probable_domain(" + incident + "," + probableDomain + ")"));
        percepts.add(Literal.parseLiteral(
                "triage_recommendation(" + incident + "," + recommendedAgent + ")"));
        for (String role : availableAgents) {
            percepts.add(Literal.parseLiteral("agent_available(" + role + ")"));
        }
    }

    public static void main(String[] args) {
        if (args.length != 5) {
            System.err.println(
                    "usage: apm-jason-bdi <asl-path> <incident-id> <domain> "
                    + "<recommended-agent> <available-agents-csv>");
            System.exit(2);
        }

        String aslPath = args[0];
        String incidentId = args[1];
        String probableDomain = args[2];
        String recommendedAgent = args[3];
        Set<String> availableAgents = new LinkedHashSet<>(Arrays.asList(args[4].split(",")));

        try {
            TechnicalLeadDecisionRunner runner = new TechnicalLeadDecisionRunner(
                    aslPath,
                    incidentId,
                    probableDomain,
                    recommendedAgent,
                    availableAgents);
            runner.runDecision();
        } catch (Exception exception) {
            System.err.println(exception.getMessage());
            System.exit(1);
        }
    }

    private void runDecision() {
        for (int cycle = 0; cycle < MAX_REASONING_CYCLES && primaryInvestigator == null; cycle++) {
            getTS().reasoningCycle();
        }

        if (primaryInvestigator == null) {
            throw new IllegalStateException(
                    "Jason completed no primary-investigator commitment within "
                    + MAX_REASONING_CYCLES + " reasoning cycles");
        }

        System.out.println(
                "OK\t" + selectedIncidentId
                + "\tmanage_incident"
                + "\tselect_primary_investigator"
                + "\t" + primaryInvestigator);
    }

    @Override
    public String getAgName() {
        return "technical_lead_bdi";
    }

    @Override
    public List<Literal> perceive() {
        return new ArrayList<>(percepts);
    }

    @Override
    public void act(ActionExec action) {
        Structure actionTerm = action.getActionTerm();
        if ("commit_primary_investigator".equals(actionTerm.getFunctor())
                && actionTerm.getArity() == 2) {
            selectedIncidentId = unquote(actionTerm.getTerm(0).toString());
            primaryInvestigator = actionTerm.getTerm(1).toString();
        }

        action.setResult(true);
        actionExecuted(action);
    }

    @Override
    public boolean canSleep() {
        return false;
    }

    @Override
    public boolean isRunning() {
        return true;
    }

    @Override
    public void sleep() {
        // The runner is explicitly stepped by runDecision().
    }

    @Override
    public void sendMsg(Message message) {
        // SPADE/XMPP owns inter-agent communication in this project.
    }

    @Override
    public void broadcast(Message message) {
        // SPADE/XMPP owns inter-agent communication in this project.
    }

    @Override
    public void checkMail() {
        // SPADE/XMPP owns inter-agent communication in this project.
    }

    private static String quote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    private static String unquote(String value) {
        if (value.length() >= 2 && value.startsWith("\"") && value.endsWith("\"")) {
            return value.substring(1, value.length() - 1);
        }
        return value;
    }
}
