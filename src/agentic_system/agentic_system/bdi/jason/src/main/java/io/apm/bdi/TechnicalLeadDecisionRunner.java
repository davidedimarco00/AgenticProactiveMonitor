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
import jason.runtime.Settings;

/**
 * Minimal custom agent architecture that uses Jason only as the BDI engine.
 * SPADE remains the communication/runtime framework of AgenticProactiveMonitor.
 */
public final class TechnicalLeadDecisionRunner extends AgArch {
    private static final int MAX_REASONING_CYCLES = 64;
    private static final String BEGIN_TRIAGE = "begin-triage";
    private static final String SELECT_PRIMARY = "select-primary";

    private final List<Literal> percepts = new ArrayList<>();
    private String selectedIncidentId;
    private String committedIntention;
    private String primaryInvestigator = "-";

    private TechnicalLeadDecisionRunner(
            String aslPath,
            String mode,
            String incidentId,
            String probableDomain,
            String recommendedAgent,
            Set<String> availableAgents) throws Exception {
        if (!BEGIN_TRIAGE.equals(mode) && !SELECT_PRIMARY.equals(mode)) {
            throw new IllegalArgumentException("Unsupported BDI mode: " + mode);
        }

        Agent agent = new Agent();
        new TransitionSystem(agent, new Circumstance(), new Settings(), this);
        agent.initAg(aslPath);

        String incident = quote(incidentId);
        percepts.add(Literal.parseLiteral("incident(" + incident + ")"));
        percepts.add(Literal.parseLiteral(
                "incident_status(" + incident + ",taken_in_charge)"));
        for (String role : availableAgents) {
            percepts.add(Literal.parseLiteral("agent_available(" + role + ")"));
        }

        if (SELECT_PRIMARY.equals(mode)) {
            percepts.add(Literal.parseLiteral("triage_complete(" + incident + ")"));
            percepts.add(Literal.parseLiteral(
                    "probable_domain(" + incident + "," + probableDomain + ")"));
            percepts.add(Literal.parseLiteral(
                    "triage_recommendation(" + incident + "," + recommendedAgent + ")"));
        }
    }

    public static void main(String[] args) {
        if (args.length != 6) {
            System.err.println(
                    "usage: apm-jason-bdi <asl-path> <mode> <incident-id> <domain-or-none> "
                    + "<recommended-agent-or-none> <available-agents-csv>");
            System.exit(2);
        }

        String aslPath = args[0];
        String mode = args[1];
        String incidentId = args[2];
        String probableDomain = args[3];
        String recommendedAgent = args[4];
        Set<String> availableAgents = new LinkedHashSet<>(Arrays.asList(args[5].split(",")));

        try {
            TechnicalLeadDecisionRunner runner = new TechnicalLeadDecisionRunner(
                    aslPath,
                    mode,
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
        for (int cycle = 0; cycle < MAX_REASONING_CYCLES && committedIntention == null; cycle++) {
            getTS().reasoningCycle();
        }

        if (committedIntention == null) {
            throw new IllegalStateException(
                    "Jason completed no Technical Lead commitment within "
                    + MAX_REASONING_CYCLES + " reasoning cycles");
        }

        System.out.println(
                "OK\t" + selectedIncidentId
                + "\tmanage_incident"
                + "\t" + committedIntention
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
        var actionTerm = action.getActionTerm();
        if ("request_triage_analysis".equals(actionTerm.getFunctor())
                && actionTerm.getArity() == 1) {
            selectedIncidentId = unquote(actionTerm.getTerm(0).toString());
            committedIntention = "triage_incident";
            primaryInvestigator = "-";
        } else if ("commit_primary_investigator".equals(actionTerm.getFunctor())
                && actionTerm.getArity() == 2) {
            selectedIncidentId = unquote(actionTerm.getTerm(0).toString());
            committedIntention = "select_primary_investigator";
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
